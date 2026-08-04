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
from gcl_sdk.agents.universal.drivers import pool as ua_pool
from gcl_sdk.agents.universal.services import builder as sdk_builder
from restalchemy.dm import filters as dm_filters
from restalchemy.dm import relationships

from exordos_core.compute.dm import models

LOG = logging.getLogger(__name__)


class Volume(
    models.Volume,
    ua_models.InstanceMixin,
    ua_models.DependenciesExistReadinessMixin,
):
    __tracked_instances_model_map__ = {
        "pool_volume": models.MachineVolume,
    }

    node = relationships.relationship(models.Node, prefetch=True)

    @classmethod
    def get_resource_kind(cls) -> str:
        return "volume"

    def get_tracked_resources(
        self,
    ) -> tp.Collection[ua_models.RI]:
        """Return the tracked resources.

        Method returns either collection of ResourceKindAwareMixin or
        ResourceIdentifier. If any of resources are changed, a special hooks
        in the UB will be called to actualize the instance.
        """
        return (ua_models.RI("pool_volume", self.uuid),)

    def get_readiness_dependencies(
        self,
    ) -> tp.Collection[ua_models.RI]:
        """Get the dependencies to check readiness."""
        return (ua_models.RI("pool_volume", self.uuid),)


class VolumeBuilderService(sdk_builder.UniversalBuilderService):
    def __init__(
        self,
        iter_min_period: int = 1,
        iter_pause: float = 0.1,
    ) -> None:
        super().__init__(
            Volume,
            iter_min_period=iter_min_period,
            iter_pause=iter_pause,
        )

    # Internal methods

    def _actualize_machine_volume(
        self, target: Volume, actual: tp.Optional[Volume] = None
    ) -> None:
        """Update volume based on actual node data."""
        # Check if volumes are already up to date
        if (
            actual is not None
            and target.size == actual.size
            and target.image == actual.image
            and target.label == actual.label
            and target.device_type == actual.device_type
            and target.boot == actual.boot
            and target.index == actual.index
            and target.node == actual.node
        ):
            return

        machine_volume = models.MachineVolume.objects.get_one_or_none(
            filters={"node_volume": dm_filters.EQ(target.uuid)}
        )
        if machine_volume is None:
            return

        # Determine machine
        machine = None
        if target.node is not None:
            machine = models.Machine.objects.get_one_or_none(
                filters={"node": dm_filters.EQ(target.node.uuid)}
            )
            machine = machine.uuid if machine is not None else None

        # Update machine volume
        machine_volume.size = target.size
        machine_volume.image = target.image
        machine_volume.label = target.label
        machine_volume.device_type = target.device_type
        machine_volume.boot = target.boot
        machine_volume.index = target.index
        machine_volume.machine = machine
        machine_volume.status = ua_pool.VolumeStatus.IN_PROGRESS.value
        machine_volume.save()

    # Builder lifecycle hooks

    def post_create_instance_resource(
        self,
        instance: Volume,
        resource: ua_models.TargetResource,
        derivatives: tp.Collection[ua_models.TargetResource] = tuple(),
    ) -> None:
        """The hook is performed after saving instance resource.

        The hook is called only for new instances.
        """
        super().post_create_instance_resource(instance, resource, derivatives)
        self._actualize_machine_volume(instance)

    def pre_update_instance_resource(
        self, instance: Volume, resource: ua_models.TargetResource
    ) -> None:
        """The hook is called before updating instance resource.

        Use this hook to actualize machine volume related to this volume.
        """
        target_volume = instance
        actual_volume = Volume.from_ua_resource(resource)

        # Update volume
        self._actualize_machine_volume(target_volume, actual_volume)

    def actualize_instance_with_outdated_tracked(
        self,
        instance: Volume,
        tracked_instances: tp.Collection[models.MachineVolume],
    ) -> None:
        """Actualize instance with outdated tracked instances.

        Use this hook to react to changes in tracked machine volume.
        For example, status changes.
        """
        if len(tracked_instances) == 1:
            instance.status = tracked_instances[0].status
