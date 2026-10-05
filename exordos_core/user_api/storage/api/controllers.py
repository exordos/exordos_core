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
                models.StoragePool(
                    uuid=sys_uuid.UUID(uuid), cluster=cluster.uuid, **policy
                ).insert()
            state.sync_cluster(cluster)
            return cluster

    def update(self, uuid, **kwargs):
        with models.StorageCluster._get_engine().session_manager() as session:
            state.lock(session)
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
            if models.StorageNode.objects.get_all(
                filters={"cluster": dm_filters.EQ(cluster.uuid)}
            ):
                raise storage_exc.ConflictRecords(
                    model="StorageCluster", msg="Remove storage nodes first"
                )
            for policy in models.StoragePool.objects.get_all(
                filters={"cluster": dm_filters.EQ(cluster.uuid)}
            ):
                policy.delete()
            return super().delete(uuid)

    def _validate_driver_spec_uniqueness(self, kwargs: dict, exclude_uuid=None) -> None:
        """Validate MDS ports and OST addresses before scheduling a cluster."""
        driver_spec = kwargs["driver_spec"]
        if not isinstance(driver_spec, dict):
            driver_spec = driver_spec.dump_to_simple_view()
        endpoint = driver_spec.get("endpoint")
        if endpoint is None:
            return
        ost_endpoint = driver_spec.get("ost_endpoint", "")
        if driver_spec.get("kind") == "rawstor":
            mds = _validate_endpoint(endpoint, "mds")
            if ost_endpoint:
                _validate_endpoint(ost_endpoint, "ost")
        else:
            mds = urlparse(endpoint)
        # JSONB field filtering is not yet supported by restalchemy.
        existing_clusters = models.StorageCluster.objects.get_all()
        for cluster in existing_clusters:
            if str(cluster.uuid) == str(exclude_uuid):
                # Existing disks and chunk maps still use these addresses.
                if (
                    cluster.driver_spec.endpoint != endpoint
                    or cluster.driver_spec.ost_endpoint != ost_endpoint
                ):
                    raise storage_exc.ConflictRecords(
                        model="StorageCluster",
                        msg="Registered MDS and OST endpoints cannot be changed",
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
            if ost_endpoint and cluster.driver_spec.ost_endpoint.rstrip(
                "/"
            ) == ost_endpoint.rstrip("/"):
                raise storage_exc.ConflictRecords(
                    model="StorageCluster", msg=f"ost_endpoint={ost_endpoint}"
                )


def _require_no_disks(cluster, pool_uuid=None):
    volumes = compute_models.MachineVolume.objects.get_all(
        filters={"storage_location": dm_filters.EQ(cluster.driver_spec.endpoint)}
    )
    if pool_uuid is not None:
        volumes = [
            v for v in volumes if v.storage_policy.get("pool_uuid") == str(pool_uuid)
        ]
    if volumes:
        raise storage_exc.ConflictRecords(
            model="StorageCluster", msg="Storage still has allocated or pending disks"
        )


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
            self.validate_member(kwargs, cluster)
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
            if "cluster" in kwargs and str(kwargs["cluster"]) != str(member.cluster):
                raise storage_exc.ConflictRecords(
                    model="StorageCluster",
                    msg="Moving members between clusters is unsupported",
                )
            data = member.dump_to_simple_view()
            data.update(kwargs)
            self.validate_member(data, cluster, member)
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
    __resource__ = resources.ResourceByRAModel(
        model_class=models.StorageNode,
        process_filters=True,
        convert_underscore=False,
    )

    def validate_member(self, data, cluster, existing=None):
        _validate_endpoint(data["endpoint"], "ost")
        weight = data.get("weight", 1)
        if not math.isfinite(weight) or weight <= 0:
            raise InvalidRawstorEndpoint(msg="OST weight must be finite and positive")
        path = data["failure_domain_path"]
        if (
            not 1 <= len(path.split("/")) <= 4
            or any(not p or p in (".", "..") for p in path.split("/"))
            or any(c.isspace() for c in path)
        ):
            raise InvalidRawstorEndpoint(
                msg="Failure domain path must be dc/row/rack/server (1 to 4 components)"
            )
        if existing and existing.endpoint != data["endpoint"]:
            _require_no_disks(cluster)
        # The SQL unique constraint also protects endpoint/name races.
        for node in models.StorageNode.objects.get_all():
            if existing and node.uuid == existing.uuid:
                continue
            if node.endpoint.rstrip("/") == data["endpoint"].rstrip("/"):
                raise storage_exc.ConflictRecords(
                    model="StorageNode", msg="OST already registered"
                )
            if node.cluster == cluster.uuid and node.name == data["name"]:
                raise storage_exc.ConflictRecords(
                    model="StorageNode", msg="Node name already used in cluster"
                )

    def validate_delete(self, member, cluster):
        # No new writes may race an OST removal. Also protect unmanaged objects.
        _require_no_disks(cluster)
        import rawstor

        if next(iter(rawstor.Location(member.endpoint)), None) is not None:
            raise storage_exc.ConflictRecords(
                model="StorageNode", msg="OST is not empty"
            )


class StoragePoolsController(ClusterMemberController):
    __resource__ = resources.ResourceByRAModel(
        model_class=models.StoragePool,
        process_filters=True,
        convert_underscore=False,
    )

    def validate_member(self, data, cluster, existing=None):
        # Apply model defaults before validating an add request.
        policy = {
            field: data.get(field, default)
            for field, default in {
                "speed": "WARM",
                "ephemeral": False,
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
