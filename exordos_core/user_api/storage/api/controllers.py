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

import math
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
            or parsed.username
            or any(c.isspace() for c in endpoint)
        ):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise InvalidRawstorEndpoint(msg=f"expected {scheme}://host:port/")
    return parsed


class StorageController(controllers.RoutesListController):
    __TARGET_PATH__ = "/v1/storage/"


class StorageClustersController(
    iam_controllers.PolicyBasedController,
    controllers.BaseResourceControllerPaginated,
):
    """Controller for /v1/storage/clusters/ endpoint"""

    __policy_name__ = "storage_cluster"
    __policy_service_name__ = sc.POLICY_SERVICE_NAME

    __resource__ = resources.ResourceByRAModel(
        model_class=models.StorageCluster,
        process_filters=True,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "status": {ra_c.ALL: field_p.Permissions.RO},
                "capacity_info": {ra_c.ALL: field_p.Permissions.RO},
                "storage_pools": {ra_c.ALL: field_p.Permissions.RO},
            },
        ),
    )

    def create(self, **kwargs):
        with models.StorageCluster._get_engine().session_manager() as session:
            state.lock(session)
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
                policy["name"] = f"{cluster.name}-{policy['name']}"
                models.StoragePool(
                    uuid=sys_uuid.UUID(uuid),
                    cluster=cluster.uuid,
                    name=policy["name"],
                    tier=policy["tier"],
                    ephemeral=policy["ephemeral"],
                    driver_spec=ua_pool.rawstor_specs.RawstorPoolDriverSpec(
                        **{
                            k: policy[k]
                            for k in ("mirrors", "chunk_size", "failure_domain")
                        }
                    ),
                ).insert()
            state.sync_cluster(cluster)
            return cluster

    def update(self, uuid, **kwargs):
        with models.StorageCluster._get_engine().session_manager() as session:
            state.lock(session)
            _require_cluster_not_deleting(self.get(uuid=uuid))
            if "driver_spec" in kwargs:
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
        driver_spec = kwargs["driver_spec"]
        if not isinstance(driver_spec, dict):
            driver_spec = driver_spec.dump_to_simple_view()
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
    if volumes:
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
    return dict(spec) if isinstance(spec, dict) else spec.dump_to_simple_view()


class ClusterMemberController(
    iam_controllers.PolicyBasedController,
    controllers.BaseResourceControllerPaginated,
):
    # Nodes and policies are governed by the same storage administrator policy.
    __policy_name__ = "storage_cluster"
    __policy_service_name__ = sc.POLICY_SERVICE_NAME

    def create(self, **kwargs):
        with self.model._get_engine().session_manager() as session:
            state.lock(session)
            cluster = models.StorageCluster.objects.get_one(
                filters={"uuid": dm_filters.EQ(kwargs["cluster"])}
            )
            _require_cluster_not_deleting(cluster)
            kwargs["driver_spec"] = _driver_spec_values(kwargs["driver_spec"])
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
                    **_driver_spec_values(kwargs["driver_spec"]),
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
        spec = _driver_spec_values(kwargs["driver_spec"])
        spec["location"] = (
            spec.get("location") or f"file:///var/lib/rawstor/ost/{kwargs['uuid']}"
        )
        endpoint = _validate_endpoint(spec["endpoint"], "ost")
        spec["bind_address"] = spec.get("bind_address") or endpoint.netloc
        kwargs["driver_spec"] = spec
        kwargs["status"] = "NEW"
        return super().create(**kwargs)

    def update(self, uuid, **kwargs):
        if "driver_spec" in kwargs:
            spec = _driver_spec_values(kwargs["driver_spec"])
            if "endpoint" in spec:
                endpoint = _validate_endpoint(spec["endpoint"], "ost")
                spec["bind_address"] = spec.get("bind_address") or endpoint.netloc
            kwargs["driver_spec"] = spec
            previous = self.get(uuid=uuid).driver_spec
            if any(
                field in spec and spec[field] != getattr(previous, field)
                for field in ("endpoint", "bind_address", "location")
            ):
                kwargs["status"] = "IN_PROGRESS"
        return super().update(uuid, **kwargs)

    def validate_member(self, data, cluster, existing=None):
        data = {**data, **data["driver_spec"]}
        _validate_endpoint(data["endpoint"], "ost")
        agent_uuid = data.get("agent")
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
        weight = data.get("weight", 100)
        if (
            not math.isfinite(weight)
            or not 1 <= weight <= (1 << 63) - 1
            or weight != int(weight)
        ):
            raise InvalidRawstorEndpoint(msg="OST weight must be a positive integer")
        path = data["failure_domain_path"]
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
                other_agent = ua_models.UniversalAgent.objects.get_one(
                    filters={"uuid": dm_filters.EQ(node.agent)}
                )
                if other_agent.node == agent.node:
                    if (
                        node.driver_spec.location == data["location"]
                        or urlparse("ost://" + node.driver_spec.bind_address).port
                        == urlparse("ost://" + data["bind_address"]).port
                    ):
                        raise storage_exc.ConflictRecords(
                            model="StorageNode",
                            msg="OST backing location or bind port already used on this host",
                        )
            if node.driver_spec.endpoint.rstrip("/") == data["endpoint"].rstrip("/"):
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
            if str(resource.uuid) == str(data.get("uuid")) or value["endpoint"].rstrip(
                "/"
            ) == data["endpoint"].rstrip("/"):
                raise storage_exc.ConflictRecords(
                    model="StorageNode",
                    msg="OST endpoint or identity is still reconciled; wait for removal",
                )
            previous_agent = value.get("agent") or resource.agent
            if agent_uuid is not None and previous_agent is not None:
                other_agent = ua_models.UniversalAgent.objects.get_one(
                    filters={"uuid": dm_filters.EQ(previous_agent)}
                )
                if other_agent.node == agent.node and (
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
        if member.agent is not None and member.status != "ACTIVE":
            actual = ua_models.Resource.objects.get_all(
                filters={
                    "uuid": dm_filters.EQ(member.uuid),
                    "kind": dm_filters.EQ("storage_node"),
                }
            )
            if not actual:
                # A never-ready OST has not been admitted into the MDS topology.
                return
        import rawstor

        if next(iter(rawstor.Location(member.driver_spec.endpoint)), None) is not None:
            raise storage_exc.ConflictRecords(
                model="StorageNode", msg="OST is not empty"
            )


class StoragePoolsController(ClusterMemberController):
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
        # Apply model defaults before validating an add request.
        data = {**data, **data.get("driver_spec", {"kind": "rawstor"})}
        policy = {
            field: data.get(field, default)
            for field, default in {
                "tier": "WARM",
                "ephemeral": True,
                "mirrors": 2,
                "chunk_size": 1 << 30,
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
