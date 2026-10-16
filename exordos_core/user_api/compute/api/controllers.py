#    Copyright 2025 Genesis Corporation.
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

import uuid as sys_uuid

from gcl_iam.api import controllers as iam_controllers
from gcl_sdk.agents.universal.drivers import pool as ua_pool
from restalchemy.api import actions
from restalchemy.api import constants as ra_c
from restalchemy.api import controllers
from restalchemy.api import field_permissions as field_p
from restalchemy.api import resources
from restalchemy.common import exceptions as ra_e
from restalchemy.dm import filters as dm_filters
from restalchemy.storage import exceptions as storage_exc

from exordos_core.common import constants as core_constants
from exordos_core.common import exceptions as ex_exceptions
from exordos_core.common.api.controllers import StoredFieldsQueryMixin
from exordos_core.common.dm import networking as networking_types
from exordos_core.compute import constants as nc
from exordos_core.compute.dm import models as models
from exordos_core.user_api.compute.dm import models as user_models


class NodeResource(resources.ResourceByRAModel):
    def generate_schema_object(self, method, openapi_version):
        schema = super().generate_schema_object(method, openapi_version)
        if method == ra_c.CREATE:
            schema["properties"]["default_network"] = (
                networking_types.DefaultNetworkRequestType().to_openapi_spec(
                    {"openapi": openapi_version}
                )
            )
            schema["x-create-only"] = list(self.get_model().__create_only_fields__)
        return schema


class NodeSetResource(resources.ResourceByRAModel):
    def generate_schema_object(self, method, openapi_version):
        schema = super().generate_schema_object(method, openapi_version)
        if method in (ra_c.CREATE, ra_c.UPDATE):
            schema["properties"]["default_network"] = (
                networking_types.DefaultNetworkRequestType(
                    template=True
                ).to_openapi_spec({"openapi": openapi_version})
            )
        return schema


class VolumeAlreadyAttachedError(ra_e.ValidationErrorException):
    message = "Volume is already attached to a node"


class VolumeNotAttachedError(ra_e.ValidationErrorException):
    message = "Volume is not attached to a node"


class ComputeController(controllers.RoutesListController):
    __TARGET_PATH__ = "/v1/compute/"


class VolumeResource(resources.ResourceByRAModel):
    def generate_schema_object(self, method, openapi_version):
        schema = super().generate_schema_object(method, openapi_version)
        if method == ra_c.CREATE:
            # Immutable after creation does not mean forbidden in a create body.
            for field in ("tier", "ephemeral"):
                schema["properties"][field].pop("readOnly", None)
        return schema


class VolumesController(
    iam_controllers.PolicyBasedController,
    controllers.BaseResourceControllerPaginated,
):
    """Controller for /v1/compute/volumes/ endpoint"""

    __policy_name__ = "volume"
    __policy_service_name__ = nc.POLICY_SERVICE_NAME

    __resource__ = VolumeResource(
        model_class=user_models.Volume,
        process_filters=True,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "status": {ra_c.ALL: field_p.Permissions.RO},
                "pool": {ra_c.ALL: field_p.Permissions.HIDDEN},
                "index": {ra_c.ALL: field_p.Permissions.HIDDEN},
            },
        ),
    )

    def update(self, uuid, **kwargs):
        kwargs["status"] = ua_pool.VolumeStatus.IN_PROGRESS.value

        return super().update(uuid, **kwargs)

    @actions.post
    def attach(self, resource: user_models.Volume, node: str):
        volume = resource.cast_to_base()

        if volume.node is not None:
            raise VolumeAlreadyAttachedError()

        node = sys_uuid.UUID(node)

        volume.node = node
        volume.update()
        return resource

    @actions.post
    def detach(self, resource: user_models.Volume):
        volume = resource.cast_to_base()

        if volume.node is None:
            raise VolumeNotAttachedError()

        volume.node = None
        volume.update()
        return resource


class ForeignSubnetMixin:
    """Declaring a leg in somebody else's subnet needs `foreign_subnet`.

    Checked on the HTTP path, the only place with a caller to ask; manifests
    write the model directly and are admin-only. The `port` kind is already
    refused across projects when adopted.
    """

    def _enforce_foreign_subnets(self, interfaces):
        if not interfaces or self._ctx_project_id is None:
            return
        for interface in interfaces:
            owner = self._interface_owner(interface)
            if owner is not None and str(owner) != str(self._ctx_project_id):
                self._enforce("foreign_subnet")
                return

    @staticmethod
    def _interface_owner(interface):
        """Whose subnet — or whose network, when the leg names the sugar."""
        if getattr(interface, "KIND", None) != "subnet":
            return None
        for ref, model in (
            (getattr(interface, "subnet", None), models.Subnet),
            (getattr(interface, "network", None), models.Network),
        ):
            if not ref:
                continue
            target = model.objects.get_one_or_none(filters={"uuid": dm_filters.EQ(ref)})
            # A dangling reference is the model's to refuse, not a 403.
            if target is not None:
                return target.project_id
        return None


class NodesController(
    StoredFieldsQueryMixin,
    ForeignSubnetMixin,
    iam_controllers.PolicyBasedController,
    controllers.BaseResourceControllerPaginated,
):
    """Controller for /v1/compute/nodes/ endpoint"""

    __policy_name__ = "node"
    __policy_service_name__ = nc.POLICY_SERVICE_NAME

    __resource__ = NodeResource(
        model_class=user_models.Node,
        process_filters=True,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "status": {ra_c.ALL: field_p.Permissions.RO},
                "network_initialized": {ra_c.ALL: field_p.Permissions.HIDDEN},
                "default_network": {
                    ra_c.ALL: field_p.Permissions.RO,
                    ra_c.CREATE: field_p.Permissions.RW,
                },
            },
        ),
    )

    def create(self, **kwargs):
        # Validate disk spec
        self.model.validate_create_input(kwargs)
        self._enforce_and_override_project_id_in_kwargs("create", kwargs)
        from exordos_core.network.permissions import gate_default_network

        gate_default_network(
            self, kwargs, kwargs.get("project_id", core_constants.ZERO_UUID)
        )
        node = self.model(**kwargs)
        node.disk_spec.validate()
        self._enforce_foreign_subnets(node.interfaces)

        from exordos_core.network.contracts import atomic_session
        from exordos_core.network.permissions import gate_allocated_private_address

        with atomic_session(self.model._get_engine()) as session:
            node = super().create(**kwargs)
            request = kwargs.get("default_network") or {}
            if request.get("target_ipv4") is not None and node.node_type == "VM":
                port = models.Port.objects.get_one(
                    filters={
                        "node": dm_filters.EQ(node.uuid),
                        "index": dm_filters.EQ(0),
                    },
                    session=session,
                )
                gate_allocated_private_address(self, port, session)
            return node

    def update(self, uuid, **kwargs):
        if "default_network" in kwargs:
            raise ex_exceptions.ValidateException(
                err="default_network is create-only; update ports directly"
            )
        kwargs["status"] = nc.NodeStatus.IN_PROGRESS.value
        if "interfaces" in kwargs:
            self._enforce_foreign_subnets(kwargs["interfaces"])

        return super().update(uuid, **kwargs)

    @actions.get
    def get_private_key(self, resource: user_models.Node):
        self._enforce("get_private_key")

        return resource.get_agent_private_key()

    @actions.get
    def details(self, resource: user_models.Node):
        """Return the details the node model itself doesn't carry."""
        self._enforce("details")

        machine = models.Machine.objects.get_one_or_none(
            filters={"node": dm_filters.EQ(resource.uuid)},
        )
        hypervisor = (
            models.MachinePool.objects.get_one_or_none(
                filters={"uuid": dm_filters.EQ(machine.pool)},
            )
            if machine is not None and machine.pool is not None
            else None
        )

        return {
            "hypervisor": (str(hypervisor.uuid) if hypervisor is not None else None),
        }


class NodeSetsController(
    ForeignSubnetMixin,
    iam_controllers.PolicyBasedController,
    controllers.BaseResourceControllerPaginated,
):
    """Controller for /v1/compute/sets/ endpoint"""

    __policy_name__ = "node_set"
    __policy_service_name__ = nc.POLICY_SERVICE_NAME

    __resource__ = NodeSetResource(
        model_class=models.NodeSet,
        process_filters=True,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "status": {ra_c.ALL: field_p.Permissions.RO},
                "nodes": {ra_c.ALL: field_p.Permissions.RO},
            },
        ),
    )

    def create(self, **kwargs):
        self.model.validate_create_input(kwargs)
        self._enforce_and_override_project_id_in_kwargs("create", kwargs)
        from exordos_core.network.permissions import gate_default_network

        gate_default_network(
            self, kwargs, kwargs.get("project_id", core_constants.ZERO_UUID)
        )
        # Validate disk spec
        node_set = self.model(**kwargs)
        node_set.disk_spec.validate()
        self._enforce_foreign_subnets(node_set.interfaces)

        return super().create(**kwargs)

    def update(self, uuid, **kwargs):
        stored = self.get(uuid=uuid)
        self.model.validate_update_input(kwargs, stored)
        from exordos_core.network.permissions import gate_default_network

        if "default_network" in kwargs:
            gate_default_network(self, kwargs, stored.project_id)
        kwargs["status"] = nc.NodeStatus.IN_PROGRESS.value
        if "interfaces" in kwargs:
            self._enforce_foreign_subnets(kwargs["interfaces"])

        return super().update(uuid, **kwargs)

    @actions.get
    def get_private_keys(self, resource: models.NodeSet):
        self._enforce("get_private_key")

        return resource.get_agents_private_keys()


class HypervisorsController(
    iam_controllers.PolicyBasedController,
    controllers.BaseResourceControllerPaginated,
):
    """Controller for /v1/compute/hypervisors/ endpoint"""

    __policy_name__ = "hypervisor"
    __policy_service_name__ = nc.POLICY_SERVICE_NAME

    __resource__ = resources.ResourceByRAModel(
        model_class=models.MachinePool,
        process_filters=True,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "status": {ra_c.ALL: field_p.Permissions.RO},
            },
        ),
    )

    def create(self, **kwargs):
        if (
            "machine_type" in kwargs
            and kwargs["machine_type"] != ua_pool.NodeType.VM.value
        ):
            raise ValueError("Hyper must be VM type")

        self._validate_driver_spec_uniqueness(kwargs)

        return super().create(**kwargs)

    def _validate_driver_spec_uniqueness(self, kwargs: dict) -> None:
        """Validate that the driver_spec fields are unique among existing pools."""
        driver_spec = kwargs["driver_spec"]
        kind = driver_spec["kind"]

        if kind == ua_pool.LibvirtPoolDriverSpec.KIND:
            connection_uri = driver_spec["connection_uri"]
            # TODO(akremenetsky): Use JSON field filter when restalchemy
            # supports filtering by JSONB fields.
            existing_pools = models.MachinePool.objects.get_all()
            for pool in existing_pools:
                if (
                    pool.driver_spec.KIND == kind
                    and pool.driver_spec.connection_uri == connection_uri
                ):
                    raise storage_exc.ConflictRecords(
                        model="MachinePool",
                        msg=f"connection_uri={connection_uri}",
                    )
        elif kind == ua_pool.ExordosLocalHyperDriverSpec.KIND:
            node = str(driver_spec["node"])
            # TODO(akremenetsky): Use JSON field filter when restalchemy
            # supports filtering by JSONB fields.
            existing_pools = models.MachinePool.objects.get_all()
            for pool in existing_pools:
                if pool.driver_spec.KIND == kind and str(pool.driver_spec.node) == node:
                    raise storage_exc.ConflictRecords(
                        model="MachinePool",
                        msg=f"node={node}",
                    )
