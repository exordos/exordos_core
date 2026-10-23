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
from gcl_sdk.infra import constants as ic
from restalchemy.api import actions
from restalchemy.api import constants as ra_c
from restalchemy.api import controllers
from restalchemy.api import field_permissions as field_p
from restalchemy.api import resources
from restalchemy.common import exceptions as ra_e
from restalchemy.dm import filters as dm_filters
from restalchemy.storage import exceptions as storage_exc

from exordos_core.compute import constants as nc
from exordos_core.compute.dm import models as models
from exordos_core.user_api.compute.dm import models as user_models


class VolumeAlreadyAttachedError(ra_e.ValidationErrorException):
    message = "Volume is already attached to a node"


class VolumeNotAttachedError(ra_e.ValidationErrorException):
    message = "Volume is not attached to a node"


class RebootNotFinishedError(ra_e.RestAlchemyException):
    code = 409
    message = "The previous reboot of the node is not finished yet"


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


class NodesController(
    iam_controllers.PolicyBasedController,
    controllers.BaseResourceControllerPaginated,
):
    """Controller for /v1/compute/nodes/ endpoint"""

    __policy_name__ = "node"
    __policy_service_name__ = nc.POLICY_SERVICE_NAME

    __resource__ = resources.ResourceByRAModel(
        model_class=user_models.Node,
        process_filters=True,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "status": {ra_c.ALL: field_p.Permissions.RO},
                "power_state": {ra_c.ALL: field_p.Permissions.RO},
                # Changed only by the power actions
                "target_power_state": {ra_c.ALL: field_p.Permissions.HIDDEN},
            },
        ),
    )

    def create(self, **kwargs):
        # Validate disk spec
        node = self.model(**kwargs)
        node.disk_spec.validate()

        return super().create(**kwargs)

    def update(self, uuid, **kwargs):
        kwargs["status"] = nc.NodeStatus.IN_PROGRESS.value

        return super().update(uuid, **kwargs)

    def _set_target_power_state(
        self, resource: user_models.Node, power_state: ic.PowerState
    ) -> user_models.Node:
        self._enforce("power")

        # The finished reboot is reported until the target is switched
        # back to `ON` and the agent notices it. A new reboot requested
        # meanwhile is taken as finished and silently skipped.
        if (
            power_state == ic.PowerState.REBOOT
            and resource.power_state == ic.PowerState.REBOOTED
        ):
            raise RebootNotFinishedError()

        # Nothing to do if the power state is already requested. Changing
        # the status leaves it `IN_PROGRESS` as nothing brings it back.
        # The pending reboot covers the new one.
        if resource.target_power_state == power_state:
            return resource

        resource.target_power_state = power_state.value
        resource.status = nc.NodeStatus.IN_PROGRESS.value
        resource.update()
        return resource

    @actions.post
    def power_on(self, resource: user_models.Node):
        return self._set_target_power_state(resource, ic.PowerState.ON)

    @actions.post
    def power_off(self, resource: user_models.Node):
        return self._set_target_power_state(resource, ic.PowerState.OFF)

    @actions.post
    def reboot(self, resource: user_models.Node):
        return self._set_target_power_state(resource, ic.PowerState.REBOOT)

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
    iam_controllers.PolicyBasedController,
    controllers.BaseResourceControllerPaginated,
):
    """Controller for /v1/compute/sets/ endpoint"""

    __policy_name__ = "node_set"
    __policy_service_name__ = nc.POLICY_SERVICE_NAME

    __resource__ = resources.ResourceByRAModel(
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
        # Validate disk spec
        node_set = self.model(**kwargs)
        node_set.disk_spec.validate()

        return super().create(**kwargs)

    def update(self, uuid, **kwargs):
        kwargs["status"] = nc.NodeStatus.IN_PROGRESS.value

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
