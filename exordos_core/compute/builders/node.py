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

import logging
import typing as tp

from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.agents.universal.services import builder as sdk_builder
from restalchemy.dm import filters as dm_filters

from exordos_core.compute import constants as nc
from exordos_core.compute.dm import models

LOG = logging.getLogger(__name__)


class Node(
    models.Node,
    ua_models.InstanceMixin,
    ua_models.DependenciesExistReadinessMixin,
):
    __tracked_instances_model_map__ = {
        "machine": models.Machine,
    }

    @classmethod
    def get_resource_kind(cls) -> str:
        return "node"

    def get_tracked_resources(
        self,
    ) -> tp.Collection[ua_models.RI]:
        """Return the tracked resources.

        Method returns either collection of ResourceKindAwareMixin or
        ResourceIdentifier. If any of resources are changed, a special hooks
        in the UB will be called to actualize the instance.
        """
        return (ua_models.RI("machine", self.uuid),)

    def get_readiness_dependencies(
        self,
    ) -> tp.Collection[ua_models.RI]:
        """Get the dependencies to check readiness."""
        return (ua_models.RI("machine", self.uuid),)


class NodeBuilderService(sdk_builder.UniversalBuilderService):
    def __init__(
        self,
        iter_min_period: int = 1,
        iter_pause: float = 0.1,
    ) -> None:
        super().__init__(
            Node,
            iter_min_period=iter_min_period,
            iter_pause=iter_pause,
        )

    # Internal methods

    def _update_machine(
        self, target_node: Node, actual_node: Node, force: bool = False
    ) -> None:
        """Update machine based on actual node data."""

        # The `hostname` is not present directly in the machine model
        # but it has in the `guest_machine` so update the machine
        # to rebuild the `guest_machine`.
        if target_node.hostname != actual_node.hostname:
            force = True

        # Check if machine configuration is already up to date
        if (
            not force
            and target_node.cores == actual_node.cores
            and target_node.ram == actual_node.ram
            and target_node.name == actual_node.name
            and target_node.description == actual_node.description
        ):
            return

        machine = models.Machine.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(target_node.uuid)}
        )

        # Perhaps it hasn't been scheduled yet
        if machine is None:
            LOG.debug("Machine with uuid %s not found", target_node.uuid)
            return

        # Skip if no actual changes
        if (
            not force
            and machine.cores == target_node.cores
            and machine.ram == target_node.ram
            and machine.name == target_node.name
            and machine.description == target_node.description
        ):
            return

        machine.cores = target_node.cores
        machine.ram = target_node.ram
        machine.name = target_node.name
        machine.description = target_node.description
        # Also update the status to IN_PROGRESS
        machine.status = nc.MachineStatus.IN_PROGRESS.value
        machine.update(force=force)

    def _is_root_volume(self, volume: models.Volume) -> bool:
        return volume.index == 0

    def _actualize_volume(self, target: models.Volume, actual: models.Volume) -> None:
        actual.size = target.size
        actual.image = target.image
        actual.name = target.name
        actual.description = target.description
        actual.boot = target.boot
        actual.device_type = target.device_type
        actual.label = target.label
        actual.status = nc.VolumeStatus.IN_PROGRESS.value
        actual.save()

    def _actualize_volumes(
        self,
        target_volumes: tp.Collection[models.Volume],
        actual_volumes: tp.Collection[models.Volume],
    ) -> bool:
        """Actualize volumes based on actual and target volumes."""
        need_update_machine = False
        target_map = {v.uuid: v for v in target_volumes}
        actual_map = {v.uuid: v for v in actual_volumes}

        # Create volumes
        for volume_uuid in target_map.keys() - actual_map.keys():
            # Need to convert as they are different types (SDK vs DM)
            sdk_volume = target_map[volume_uuid]
            view = sdk_volume.dump_to_simple_view()
            volume = models.Volume.restore_from_simple_view(**view)
            volume.insert()

        # Delete volumes
        delete_uuids = actual_map.keys() - target_map.keys()
        if delete_uuids:
            for volume in models.Volume.objects.get_all(
                filters={"uuid": dm_filters.In(delete_uuids)}
            ):
                volume.delete()

        # Update volumes
        need_update = {}
        for volume_uuid in target_map.keys() & actual_map.keys():
            target = target_map[volume_uuid]
            actual = actual_map[volume_uuid]

            # Find volume required to be updated
            if (
                target.size != actual.size
                or target.image != actual.image
                or target.label != actual.label
                or target.device_type != actual.device_type
                or target.boot != actual.boot
            ):
                # If the root volume is updated, we need to update the machine
                if self._is_root_volume(target):
                    need_update_machine = True

                need_update[volume_uuid] = target

        # Fetch volumes that need updating
        if need_update:
            volumes = models.Volume.objects.get_all(
                filters={"uuid": dm_filters.In(need_update.keys())}
            )
            for volume in volumes:
                self._actualize_volume(need_update[volume.uuid], volume)

        return need_update_machine

    def _update_volumes(self, target_node: Node, actual_node: Node) -> bool:
        """Update volumes based on actual node data."""
        target_disk_spec = target_node.disk_spec
        actual_disk_spec = actual_node.disk_spec

        # Compare disk specs and update if needed
        if target_disk_spec == actual_disk_spec:
            return False

        # Get volumes from disk specs
        target_volumes = target_disk_spec.volumes(
            target_node, project_id=target_node.volume_project_id
        )
        actual_volumes = actual_disk_spec.volumes(actual_node)

        return self._actualize_volumes(target_volumes, actual_volumes)

    # Builder lifecycle hooks

    def pre_update_instance_resource(
        self, instance: Node, resource: ua_models.TargetResource
    ) -> None:
        """The hook is called before updating instance resource.

        Use this hook to actualize machine related to this node.
        """
        target_node = instance
        actual_node = Node.from_ua_resource(resource)

        # Update volumes
        need_update_machine = self._update_volumes(target_node, actual_node)

        # Update machine
        self._update_machine(target_node, actual_node, need_update_machine)

    def actualize_instance_with_outdated_tracked(
        self,
        instance: Node,
        tracked_instances: tp.Collection[models.Machine],
    ) -> None:
        """Actualize instance with outdated tracked instances.

        Use this hook to react to changes in tracked machine.
        For example, status changes.
        """
        if len(tracked_instances) == 1:
            instance.status = tracked_instances[0].status
