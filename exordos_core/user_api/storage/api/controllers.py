#    Copyright 2026 Genesis Corporation.
#
#    All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import ipaddress
import re
from urllib.parse import urlparse
import uuid as sys_uuid

from gcl_iam.api import controllers as iam_controllers
from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.agents.universal.drivers import pool as ua_pool
from gcl_sdk.agents.universal.drivers import storage_capacity
from restalchemy.api import constants as ra_c
from restalchemy.api import controllers
from restalchemy.api import field_permissions as field_p
from restalchemy.api import resources
from restalchemy.common import exceptions as ra_exc
from restalchemy.dm import filters as dm_filters
from restalchemy.storage import exceptions as storage_exc

from exordos_core.compute.dm import models as compute_models
from exordos_core.storage.placement import (
    references_remote_pool as _references_remote_pool,
)
from exordos_core.storage import constants as sc
from exordos_core.storage import state
from exordos_core.storage.dm import models


class InvalidRawstorEndpoint(ra_exc.ValidationErrorException):
    message = "Invalid rawstor endpoint: %(msg)s"


def _validate_endpoint(endpoint, scheme):
    try:
        parsed = urlparse(endpoint)
        if (
            not isinstance(endpoint, str)
            or parsed.scheme != scheme
            or not parsed.hostname
            or not parsed.port
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
            or any(c.isspace() for c in endpoint)
        ):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise InvalidRawstorEndpoint(msg=f"expected {scheme}://host:port/")
    _endpoint_host(parsed.hostname)
    return parsed


def _endpoint_host(host):
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        normalized = host.lower().removesuffix(".")
        labels = normalized.split(".")
        if (
            len(normalized) > 253
            or any(
                not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in labels
            )
            or all(re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]+)", label) for label in labels)
        ):
            raise InvalidRawstorEndpoint(
                msg="A valid DNS hostname or IPv4 address is required"
            )
        return normalized
    if address.version != 4 or address.is_unspecified:
        raise InvalidRawstorEndpoint(
            msg="Advertised endpoints require an IPv4 address or DNS name"
        )
    return str(address)


def _endpoint_identity(endpoint):
    parsed = _validate_endpoint(endpoint, urlparse(endpoint).scheme)
    return _endpoint_host(parsed.hostname), parsed.port


def _canonical_endpoint(endpoint):
    return f"{endpoint.scheme}://{_endpoint_host(endpoint.hostname)}:{endpoint.port}"


def _default_bind_address(endpoint):
    try:
        address = ipaddress.ip_address(endpoint.hostname)
    except ValueError:
        return f"{endpoint.hostname}:{endpoint.port}"
    if address.is_unspecified:
        raise InvalidRawstorEndpoint(
            msg="An explicit bind_address is required for a wildcard endpoint"
        )
    host = f"[{address}]" if address.version == 6 else str(address)
    return f"{host}:{endpoint.port}"


class StorageController(controllers.RoutesListController):
    __TARGET_PATH__ = "/v1/storage/"


class StorageClusterResource(resources.ResourceByRAModel):
    def generate_schema_object(self, method, openapi_version):
        schema = super().generate_schema_object(method, openapi_version)
        if method == ra_c.CREATE:
            for field in ("nodes", "size", "cpu", "ram"):
                schema["properties"][field].pop("readOnly", None)
        return schema


class StorageClustersController(
    iam_controllers.PolicyBasedController,
    controllers.BaseResourceControllerPaginated,
):
    """Controller for /v1/storage/clusters/ endpoint"""

    __policy_name__ = "storage_cluster"
    __policy_service_name__ = sc.POLICY_SERVICE_NAME

    __resource__ = StorageClusterResource(
        model_class=models.StorageCluster,
        process_filters=True,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "status": {ra_c.ALL: field_p.Permissions.RO},
                "capacity_info": {ra_c.ALL: field_p.Permissions.RO},
                "storage_pools": {ra_c.ALL: field_p.Permissions.RO},
                "agent": {ra_c.ALL: field_p.Permissions.RO},
                "builder": {ra_c.ALL: field_p.Permissions.RO},
            },
        ),
    )

    def create(self, **kwargs):
        with models.StorageCluster._get_engine().session_manager() as session:
            state.lock(session)
            kwargs.setdefault("name", "")
            kwargs.setdefault("uuid", sys_uuid.uuid4())
            self._validate_driver_spec_uniqueness(kwargs)
            if isinstance(kwargs["driver_spec"], dict):
                kwargs["driver_spec"] = (
                    ua_pool.RawstorStorageClusterDriverSpec.restore_from_simple_view(
                        **kwargs["driver_spec"]
                    )
                )
            kwargs.setdefault("uuid", sys_uuid.uuid4())
            if models.StorageCluster.objects.get_all(
                filters={"name": dm_filters.EQ(kwargs["name"])}
            ):
                raise storage_exc.ConflictRecords(
                    model="StorageCluster", msg="Cluster name is already used"
                )
            cluster = super().create(**kwargs)
            for uuid, policy in storage_capacity.default_policies(cluster.uuid).items():
                policy["name"] = f"{cluster.name[:244]}-{policy['name']}"
                models.StoragePool(
                    uuid=sys_uuid.UUID(uuid),
                    cluster=cluster.uuid,
                    name=policy["name"],
                    tier=policy["tier"],
                    ephemeral=policy["ephemeral"],
                    driver_spec=ua_pool.rawstor_specs.RawstorPoolDriverSpec(
                        **{k: policy[k] for k in ("mirrors", "failure_domain")}
                    ),
                ).insert()
            state.sync_cluster(cluster)
            return cluster

    def update(self, uuid, **kwargs):
        with models.StorageCluster._get_engine().session_manager() as session:
            state.lock(session)
            current = self.get(uuid=uuid)
            _require_cluster_not_deleting(current)
            if "driver_spec" in kwargs:
                kwargs["driver_spec"] = {
                    **current.driver_spec.dump_to_simple_view(),
                    **_driver_spec_values(kwargs.get("driver_spec")),
                }
                self._validate_driver_spec_uniqueness(kwargs, exclude_uuid=uuid)
                if isinstance(kwargs["driver_spec"], dict):
                    kwargs["driver_spec"] = (
                        ua_pool.RawstorStorageClusterDriverSpec.restore_from_simple_view(
                            **kwargs["driver_spec"]
                        )
                    )
            cluster = super().update(uuid, **kwargs)
            state.sync_cluster(cluster)
            return cluster

    def delete(self, uuid):
        with models.StorageCluster._get_engine().session_manager() as session:
            state.lock(session)
            cluster = self.get(uuid=uuid)
            _require_no_disks(cluster)
            members = models.StorageNode.objects.get_all(
                filters={"cluster": dm_filters.EQ(cluster.uuid)}
            )
            # Validate all members before deleting any of them.
            for member in members:
                StorageNodesController.validate_delete(member, cluster)
            cluster.status = sc.CLUSTER_DELETING
            cluster.update()
            for member in members:
                member.delete()
            if not state.finish_cluster_deletion(cluster):
                state.sync_cluster(cluster)

    def _validate_driver_spec_uniqueness(self, kwargs: dict, exclude_uuid=None) -> None:
        """Validate the public cluster configuration and unique MDS port."""
        driver_spec = _driver_spec_values(kwargs.get("driver_spec"))
        if driver_spec.get("kind") != ua_pool.RawstorStorageClusterDriverSpec.KIND:
            raise InvalidRawstorEndpoint(
                msg="A supported cluster driver kind is required"
            )
        unknown = set(driver_spec) - {"kind", "endpoint"}
        if unknown:
            raise InvalidRawstorEndpoint(
                msg=f"Unsupported cluster settings: {', '.join(sorted(unknown))}; configure nodes and pools separately"
            )
        endpoint = driver_spec.get("endpoint")
        if endpoint is None:
            return
        if driver_spec.get("kind") == "rawstor":
            mds = _validate_endpoint(endpoint, "mds")
        else:
            mds = urlparse(endpoint)
        _check_core_mds_port(mds.port)
        # JSONB field filtering is not yet supported by restalchemy.
        existing_clusters = models.StorageCluster.objects.get_all()
        for cluster in existing_clusters:
            if str(cluster.uuid) == str(exclude_uuid):
                # Existing disks and chunk maps still use these addresses.
                if cluster.driver_spec.endpoint != endpoint:
                    raise storage_exc.ConflictRecords(
                        model="StorageCluster",
                        msg="Registered MDS endpoint cannot be changed",
                    )
                continue
            if cluster.driver_spec.KIND != driver_spec["kind"]:
                continue
            existing_endpoint = urlparse(cluster.driver_spec.endpoint)
            if cluster.driver_spec.endpoint == endpoint or (
                mds.scheme == "mds"
                and existing_endpoint.scheme == "mds"
                and existing_endpoint.port == mds.port
            ):
                raise storage_exc.ConflictRecords(
                    model="StorageCluster", msg=f"MDS port={mds.port}"
                )

        # SQL rows disappear before the agent has stopped MDS. Its target
        # and actual resources continue to reserve the port during teardown.
        for model in (ua_models.TargetResource, ua_models.Resource):
            for resource in model.objects.get_all(
                filters={"kind": dm_filters.EQ("storage_cluster")}
            ):
                if str(resource.uuid) == str(exclude_uuid):
                    continue
                if str(resource.uuid) == str(kwargs.get("uuid")):
                    raise storage_exc.ConflictRecords(
                        model="StorageCluster",
                        msg="Cluster UUID is still reconciled; wait for removal",
                    )
                spec = resource.value["driver_spec"]
                if (
                    spec["kind"] == driver_spec["kind"]
                    and urlparse(spec["endpoint"]).port == mds.port
                ):
                    raise storage_exc.ConflictRecords(
                        model="StorageCluster",
                        msg="MDS port is still reconciled; wait for removal",
                    )


def _core_storage_hosts():
    return {
        a.node
        for a in ua_models.UniversalAgent.objects.get_all()
        if "storage_cluster" in a.list_capabilities
    }


def _mds_ports():
    ports = {
        urlparse(c.driver_spec.endpoint).port
        for c in models.StorageCluster.objects.get_all()
    }
    for model in (ua_models.TargetResource, ua_models.Resource):
        for r in model.objects.get_all(
            filters={"kind": dm_filters.EQ("storage_cluster")}
        ):
            ports.add(urlparse(r.value["driver_spec"]["endpoint"]).port)
    return ports


def _check_core_ost_port(agent, bind_address):
    if (
        agent.node in _core_storage_hosts()
        and urlparse("ost://" + bind_address).port in _mds_ports()
    ):
        raise storage_exc.ConflictRecords(
            model="StorageNode", msg="OST port is reserved by MDS on Core"
        )


def _check_core_mds_port(port):
    hosts = _core_storage_hosts()
    for resource in [*models.StorageNode.objects.get_all(), *_node_resources()]:
        data = (
            resource.dump_to_simple_view()
            if isinstance(resource, models.StorageNode)
            else resource.value
        )
        spec = data["driver_spec"]
        if not isinstance(spec, dict):
            spec = spec.dump_to_simple_view()
        if urlparse("ost://" + spec.get("bind_address", "")).port != port:
            continue
        agents = ua_models.UniversalAgent.objects.get_all(
            filters={"uuid": dm_filters.EQ(data.get("agent"))}
        )
        if not agents or agents[0].node in hosts:
            raise storage_exc.ConflictRecords(
                model="StorageCluster", msg="MDS port is reserved by an OST on Core"
            )


def _require_cluster_not_deleting(cluster):
    if cluster.status == sc.CLUSTER_DELETING:
        raise storage_exc.ConflictRecords(
            model="StorageCluster", msg="Cluster is being deleted"
        )


def _require_no_disks(cluster, pool_uuid=None):
    pool_ids = (
        [str(pool_uuid)]
        if pool_uuid is not None
        else [
            str(p.uuid)
            for p in models.StoragePool.objects.get_all(
                filters={"cluster": dm_filters.EQ(cluster.uuid)}
            )
        ]
    )
    volumes = compute_models.MachineVolume.objects.get_all(
        filters={"storage_pool": dm_filters.In(pool_ids)}
    )
    local_pools = {}
    has_volumes = any(
        _references_remote_pool(
            {
                "storage_pool": volume.storage_pool,
                "pool": volume.pool,
                "storage_backend": getattr(volume, "storage_backend", None),
            },
            pool_ids,
            local_pools,
        )
        for volume in volumes
    )
    pending_resources = any(
        _references_remote_pool(resource.value, pool_ids, local_pools)
        for model in (ua_models.TargetResource, ua_models.Resource)
        for resource in model.objects.get_all(
            filters={"kind": dm_filters.In(("pool_volume", "storage_volume"))}
        )
    )
    if has_volumes or pending_resources:
        raise storage_exc.ConflictRecords(
            model="StorageCluster", msg="Storage still has allocated or pending disks"
        )


def _node_resources():
    # Actual resources reserve their ports until the host acknowledges stopping.
    for model in (ua_models.TargetResource, ua_models.Resource):
        yield from model.objects.get_all(
            filters={"kind": dm_filters.EQ("storage_node")}
        )


def _driver_spec_values(spec):
    if isinstance(spec, dict):
        return dict(spec)
    if isinstance(
        spec,
        (
            ua_pool.RawstorStorageClusterDriverSpec,
            ua_pool.rawstor_specs.RawstorNodeDriverSpec,
            ua_pool.rawstor_specs.RawstorPoolDriverSpec,
        ),
    ):
        return spec.dump_to_simple_view()
    raise InvalidRawstorEndpoint(msg="driver_spec must be an object")


def _cluster_uuid(data):
    try:
        return sys_uuid.UUID(str(data.get("cluster")))
    except (ValueError, TypeError):
        raise InvalidRawstorEndpoint(msg="A valid cluster UUID is required")


class ClusterMemberController(
    iam_controllers.PolicyBasedController,
    controllers.BaseResourceControllerPaginated,
):
    __policy_service_name__ = sc.POLICY_SERVICE_NAME

    def create(self, **kwargs):
        with self.model._get_engine().session_manager() as session:
            state.lock(session)
            cluster = models.StorageCluster.objects.get_one(
                filters={"uuid": dm_filters.EQ(_cluster_uuid(kwargs))}
            )
            _require_cluster_not_deleting(cluster)
            kwargs.setdefault("name", "")
            kwargs["driver_spec"] = _driver_spec_values(kwargs.get("driver_spec"))
            self.validate_member(kwargs, cluster)
            kwargs["driver_spec"] = self.driver_spec_model.restore_from_simple_view(
                **kwargs["driver_spec"]
            )
            member = super().create(**kwargs)
            state.sync_cluster(cluster)
            return member

    def update(self, uuid, **kwargs):
        with self.model._get_engine().session_manager() as session:
            state.lock(session)
            member = self.get(uuid=uuid)
            cluster = models.StorageCluster.objects.get_one(
                filters={"uuid": dm_filters.EQ(member.cluster)}
            )
            _require_cluster_not_deleting(cluster)
            if "cluster" in kwargs and str(kwargs["cluster"]) != str(member.cluster):
                raise storage_exc.ConflictRecords(
                    model="StorageCluster",
                    msg="Moving members between clusters is unsupported",
                )
            data = member.dump_to_simple_view()
            if "driver_spec" in kwargs:
                kwargs["driver_spec"] = {
                    **data["driver_spec"],
                    **_driver_spec_values(kwargs.get("driver_spec")),
                }
            data.update(kwargs)
            self.validate_member(data, cluster, member)
            if "driver_spec" in kwargs:
                kwargs["driver_spec"] = self.driver_spec_model.restore_from_simple_view(
                    **kwargs["driver_spec"]
                )
            member = super().update(uuid, **kwargs)
            state.sync_cluster(cluster)
            return member

    def delete(self, uuid):
        with self.model._get_engine().session_manager() as session:
            state.lock(session)
            member = self.get(uuid=uuid)
            cluster = models.StorageCluster.objects.get_one(
                filters={"uuid": dm_filters.EQ(member.cluster)}
            )
            self.validate_delete(member, cluster)
            super().delete(uuid)
            state.sync_cluster(cluster)


class StorageNodesController(ClusterMemberController):
    __policy_name__ = "storage_node"
    driver_spec_model = ua_pool.rawstor_specs.RawstorNodeDriverSpec
    __resource__ = resources.ResourceByRAModel(
        model_class=models.StorageNode,
        process_filters=True,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "builder": {ra_c.ALL: field_p.Permissions.RO},
                "status": {ra_c.ALL: field_p.Permissions.RO},
            },
        ),
    )

    def create(self, **kwargs):
        kwargs.setdefault("uuid", sys_uuid.uuid4())
        spec = _driver_spec_values(kwargs.get("driver_spec"))
        spec["location"] = (
            spec.get("location") or f"file:///var/lib/rawstor/ost/{kwargs['uuid']}"
        )
        endpoint = _validate_endpoint(spec.get("endpoint"), "ost")
        spec["endpoint"] = _canonical_endpoint(endpoint)
        spec["bind_address"] = spec.get("bind_address") or _default_bind_address(
            endpoint
        )
        kwargs["driver_spec"] = spec
        kwargs["status"] = "NEW"
        return super().create(**kwargs)

    def update(self, uuid, **kwargs):
        if "driver_spec" in kwargs:
            spec = _driver_spec_values(kwargs.get("driver_spec"))
            if "endpoint" in spec:
                endpoint = _validate_endpoint(spec.get("endpoint"), "ost")
                spec["endpoint"] = _canonical_endpoint(endpoint)
                if "bind_address" in spec:
                    spec["bind_address"] = spec[
                        "bind_address"
                    ] or _default_bind_address(endpoint)
            kwargs["driver_spec"] = spec
            previous = self.get(uuid=uuid)
            if previous.agent is not None and any(
                field in spec and spec[field] != getattr(previous.driver_spec, field)
                for field in ("endpoint", "bind_address", "location")
            ):
                kwargs["status"] = "IN_PROGRESS"
        return super().update(uuid, **kwargs)

    def validate_member(self, data, cluster, existing=None):
        data = {**data, **data["driver_spec"]}
        _validate_endpoint(data["endpoint"], "ost")
        agent_uuid = data.get("agent")
        if agent_uuid is not None:
            try:
                if not isinstance(agent_uuid, (str, sys_uuid.UUID)):
                    raise ValueError()
                agent_uuid = sys_uuid.UUID(str(agent_uuid))
            except (ValueError, TypeError, AttributeError):
                raise InvalidRawstorEndpoint(msg="Agent must be a valid UUID")
        if agent_uuid is None and existing is None:
            raise InvalidRawstorEndpoint(msg="An OST agent is required")
        if existing and (
            str(existing.agent) != str(agent_uuid)
            or existing.driver_spec.location != data.get("location", "")
        ):
            raise storage_exc.ConflictRecords(
                model="StorageNode", msg="OST agent and backing location cannot change"
            )
        if agent_uuid is not None:
            try:
                storage_capacity.validate_ost_configuration(
                    data["location"], data["bind_address"]
                )
            except (ValueError, TypeError) as error:
                raise InvalidRawstorEndpoint(msg=str(error))
            agent = ua_models.UniversalAgent.objects.get_one(
                filters={"uuid": dm_filters.EQ(agent_uuid)}
            )
            if "storage_node" not in agent.list_capabilities:
                raise InvalidRawstorEndpoint(
                    msg="Agent does not support storage_node; run storages nodes init"
                )
        if agent_uuid is not None:
            _check_core_ost_port(agent, data["bind_address"])
        weight = data.get("weight", 100)
        if (
            isinstance(weight, bool)
            or not isinstance(weight, (int, float))
            or not 1 <= weight <= (1 << 63) - 1
            or weight != int(weight)
        ):
            raise InvalidRawstorEndpoint(msg="OST weight must be a positive integer")
        path = data.get("failure_domain_path")
        if not isinstance(path, str):
            raise InvalidRawstorEndpoint(msg="A failure domain path is required")
        if (
            not 1 <= len(path.split("/")) <= 4
            or any(not p or p in (".", "..") for p in path.split("/"))
            or any(c.isspace() for c in path)
        ):
            raise InvalidRawstorEndpoint(
                msg="Failure domain path must be dc/row/rack/server (1 to 4 components)"
            )
        if existing and (
            existing.driver_spec.endpoint != data["endpoint"]
            or existing.driver_spec.bind_address != data.get("bind_address", "")
        ):
            _require_no_disks(cluster)
        # The SQL unique constraint also protects endpoint/name races.
        for node in models.StorageNode.objects.get_all():
            if existing and node.uuid == existing.uuid:
                continue
            if agent_uuid is not None and node.agent is not None:
                previous_agents = ua_models.UniversalAgent.objects.get_all(
                    filters={"uuid": dm_filters.EQ(node.agent)}
                )
                collision = (
                    node.driver_spec.location == data["location"]
                    or urlparse("ost://" + node.driver_spec.bind_address).port
                    == urlparse("ost://" + data["bind_address"]).port
                )
                if collision and not previous_agents:
                    raise storage_exc.ConflictRecords(
                        model="StorageNode",
                        msg="An existing OST has an unresolved agent; recover or remove its registration before reusing host resources",
                    )
                if collision and previous_agents[0].node == agent.node:
                    raise storage_exc.ConflictRecords(
                        model="StorageNode",
                        msg="OST backing location or bind port already used on this host",
                    )
            if _endpoint_identity(node.driver_spec.endpoint) == _endpoint_identity(
                data["endpoint"]
            ):
                raise storage_exc.ConflictRecords(
                    model="StorageNode", msg="OST already registered"
                )
            if node.cluster == cluster.uuid and node.name == data["name"]:
                raise storage_exc.ConflictRecords(
                    model="StorageNode", msg="Node name already used in cluster"
                )
        for resource in _node_resources():
            if existing and resource.uuid == existing.uuid:
                continue
            value = {**resource.value, **resource.value["driver_spec"]}
            if str(resource.uuid) == str(data.get("uuid")) or _endpoint_identity(
                value["endpoint"]
            ) == _endpoint_identity(data["endpoint"]):
                raise storage_exc.ConflictRecords(
                    model="StorageNode",
                    msg="OST endpoint or identity is still reconciled; wait for removal",
                )
            previous_agent = value.get("agent") or resource.agent
            if agent_uuid is not None and previous_agent is not None:
                previous_agents = ua_models.UniversalAgent.objects.get_all(
                    filters={"uuid": dm_filters.EQ(previous_agent)}
                )
                if (not previous_agents or previous_agents[0].node == agent.node) and (
                    value["location"] == data["location"]
                    or urlparse("ost://" + value["bind_address"]).port
                    == urlparse("ost://" + data["bind_address"]).port
                ):
                    raise storage_exc.ConflictRecords(
                        model="StorageNode",
                        msg="OST backing location or port is still reconciled on this host; wait for removal",
                    )

    @staticmethod
    def validate_delete(member, cluster):
        # No new writes may race an OST removal. Also protect unmanaged objects.
        _require_no_disks(cluster)
        import rawstor

        try:
            rawstor.initialize(insecure=True)
            nonempty = (
                next(
                    iter(rawstor.Location(member.driver_spec.endpoint).objects()), None
                )
                is not None
            )
        except (OSError, RuntimeError) as error:
            raise storage_exc.ConflictRecords(
                model="StorageNode",
                msg="Cannot confirm the OST is empty; restore its endpoint before deletion",
            ) from error
        if nonempty:
            raise storage_exc.ConflictRecords(
                model="StorageNode", msg="OST is not empty"
            )


class StoragePoolsController(ClusterMemberController):
    __policy_name__ = "storage_pool"
    driver_spec_model = ua_pool.rawstor_specs.RawstorPoolDriverSpec
    __resource__ = resources.ResourceByRAModel(
        model_class=models.StoragePool,
        process_filters=True,
        convert_underscore=False,
    )

    def create(self, **kwargs):
        kwargs.setdefault("driver_spec", {"kind": "rawstor"})
        return super().create(**kwargs)

    def validate_member(self, data, cluster, existing=None):
        if existing is not None:
            before = existing.dump_to_simple_view()
            if (
                any(
                    data.get(field, before[field]) != before[field]
                    for field in ("tier", "ephemeral")
                )
                or _driver_spec_values(data["driver_spec"]) != before["driver_spec"]
            ):
                _require_no_disks(cluster, existing.uuid)
        # Apply model defaults before validating an add request.
        data = {**data, **data.get("driver_spec", {"kind": "rawstor"})}
        policy = {
            field: data.get(field, default)
            for field, default in {
                "tier": "WARM",
                "ephemeral": True,
                "mirrors": 2,
                "failure_domain": "server",
            }.items()
        }
        try:
            storage_capacity.validate_policy(policy)
        except (ValueError, TypeError) as error:
            raise InvalidRawstorEndpoint(msg=str(error))
        for pool in models.StoragePool.objects.get_all(
            filters={"cluster": dm_filters.EQ(cluster.uuid)}
        ):
            if pool.name == data["name"] and (
                existing is None or pool.uuid != existing.uuid
            ):
                raise storage_exc.ConflictRecords(
                    model="StoragePool", msg="Pool name already used in cluster"
                )

    def validate_delete(self, member, cluster):
        _require_no_disks(cluster, member.uuid)
