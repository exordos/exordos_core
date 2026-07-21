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

import functools
import logging
import typing as tp
import uuid as sys_uuid

from gcl_sdk.agents.universal.clients.orch import base as orch_base
from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.agents.universal.services import builder as sdk_builder
from gcl_sdk.agents.universal.services import common as sdk_svc_common
from restalchemy.dm import filters as dm_filters

from exordos_core.compute import constants as nc
from exordos_core.compute.dm import models
from exordos_core.compute.pool.dm import models as pool_models
from exordos_core.repo.dm import models as repo_models

LOG = logging.getLogger(__name__)


class PoolBuilderService(sdk_builder.CollectionUniversalBuilderService):
    def __init__(
        self,
        uuid: sys_uuid.UUID,
        orch_client: orch_base.AbstractOrchClient,
        iter_min_period: int = 1,
        iter_pause: float = 0.1,
    ) -> None:
        svc_spec = sdk_svc_common.UAServiceSpec(
            uuid=uuid,
            orch_client=orch_client,
            capabilities=(
                "builder_pool",
                "builder_pool_machine",
                "builder_pool_volume",
            ),
            name=f"compute_pool_builder {str(uuid)[:8]}",
        )

        super().__init__(
            instance_models=(
                pool_models.Pool,
                pool_models.MachineVolume,
                pool_models.Machine,
            ),
            service_spec=svc_spec,
            iter_min_period=iter_min_period,
            iter_pause=iter_pause,
        )

    # Internal methods

    def _agent_by_pool(self, pool_uuid: sys_uuid.UUID) -> sys_uuid.UUID:
        for pool in self._iteration_context["clause_filters"]["pools"]:
            if pool.uuid == pool_uuid:
                return pool.agent

        raise ValueError(f"Pool {pool_uuid} not found")

    # Machine

    def _is_core_machine(self, machine: pool_models.Machine) -> bool:
        """Determine if the machine belongs to the core set."""
        # NOTE(akremenetsky): We don't have any metadata information in
        # nodes/machines except the name and description. So the first
        # implementation is pretty straightforward and just checks the
        # machine name. In the future versions we need to associate metadata
        # to the machine and keep this info there.
        core_machine_name_prefix = "core-set-node"
        return machine.name.startswith(core_machine_name_prefix)

    def _set_machine_ctx(
        self,
        machine: pool_models.Machine,
        ports: tp.Collection[models.Port],
        volumes: tp.Collection[models.MachineVolume],
    ) -> None:
        """Set the machine context."""
        self._iteration_context[machine.uuid] = {
            "machine_port": ports[0],
            "root_volume": volumes[0],
        }

    def _get_machine_ctx(
        self,
        machine: pool_models.Machine,
    ) -> tp.Optional[tp.Tuple[models.Port, models.MachineVolume]]:
        """Get the machine context."""
        if machine.uuid not in self._iteration_context:
            return None

        port = self._iteration_context[machine.uuid]["machine_port"]
        volume = self._iteration_context[machine.uuid]["root_volume"]
        return port, volume

    def _fetch_machine_deps(
        self,
        machine: pool_models.Machine,
    ) -> tp.Tuple[tp.Collection[models.Port], tp.Collection[models.MachineVolume]]:
        """Fetch the machine dependencies."""
        ports = models.Port.objects.get_all(
            filters={"node": dm_filters.EQ(machine.node.uuid)}
        )
        volumes = models.MachineVolume.objects.get_all(
            filters={"machine": dm_filters.EQ(machine.uuid)}
        )
        return ports, sorted(volumes, key=lambda v: v.index)

    def _get_or_fetch_machine_ctx(
        self,
        machine: pool_models.Machine,
    ) -> tp.Tuple[tp.Optional[models.Port], tp.Optional[models.MachineVolume]]:
        """Get or fetch the machine context."""
        # Prepare dependencies. Firstly try to get them from the iteration
        # context. If they are not found, fetch them from the database.
        machine_ctx = self._get_machine_ctx(machine)
        if machine_ctx is None:
            ports, volumes = self._fetch_machine_deps(machine)
            self._set_machine_ctx(machine, ports, volumes)
            machine_ctx = self._get_machine_ctx(machine)

        return machine_ctx

    def _reschedule_machine(
        self,
        machine: pool_models.Machine,
    ) -> None:
        """Reschedule the machine."""
        # The scheduler will try to schedule the node again
        machine.delete()

    def _has_enough_resources_in_pool(
        self,
        pool: pool_models.Pool,
        target_machine: pool_models.Machine,
        actual_machine: tp.Optional[pool_models.Machine] = None,
    ) -> bool:
        """Check if the pool has enough resources to create the machine."""
        # Calculate how many resources we need to create the machine
        cores = actual_machine.cores if actual_machine is not None else 0
        ram = actual_machine.ram if actual_machine is not None else 0
        cores = target_machine.cores - cores
        ram = target_machine.ram - ram

        # TODO(akremenetsky): Actually we need to actualize the pool
        # avail cores and ram after allocating the machine.
        return pool.avail_cores >= cores and pool.avail_ram >= ram

    def _can_create_machine(
        self,
        machine: pool_models.Machine,
    ) -> bool:
        """Check if the machine can be created."""

        # Check pool has enough resources
        if not self._has_enough_resources_in_pool(machine.pool, machine):
            LOG.warning("Pool %s has not enough resources", machine.pool)

            # Seems the scheduler missed the pool. Try to reschedule
            # the machine
            self._reschedule_machine(machine)
            return False

        # Check deps are ready
        ports, volumes = self._fetch_machine_deps(machine)
        if not ports or not volumes:
            LOG.warning("Machine %s deps are not ready", machine.uuid)
            return False

        # Check if the port is already allocated
        if ports[0].status != nc.PortStatus.ACTIVE:
            LOG.debug("Port %s is not active", ports[0].uuid)
            return False

        # FIXME(akremenetsky): No need to check volume status as we don't
        # want to wait until it is ready. Agent firstly create a volume and
        # then attach it to the machine.

        self._set_machine_ctx(machine, ports, volumes)
        return True

    def _can_update_machine(
        self,
        machine: pool_models.Machine,
        resource: ua_models.TargetResource,
    ) -> bool:
        """Check if the machine can be updated."""
        target_machine = machine
        actual_machine = pool_models.Machine.from_ua_resource(resource)

        # Check pool has enough resources
        if not self._has_enough_resources_in_pool(
            machine.pool, target_machine, actual_machine
        ):
            # FIXME(akremenetsky): It's more safer do nothing and set the
            # machine status to ERROR than to try reschedule machine. We
            # don't know the machine is stateless or stateful.
            LOG.warning("Pool %s has not enough resources", machine.pool)
            machine.status = nc.MachineStatus.ERROR.value
            machine.node.status = nc.NodeStatus.ERROR.value
            machine.save()
            machine.node.save()
            return False

        return True

    def _resolve_image(self, image: str | None) -> str | None:
        """Resolve image URN to URI if needed.

        If the image is a URN (e.g. ``urn:images:<uuid>``), look up the
        corresponding :class:`RepoArtifact` and return its ``uri``.
        Otherwise return the image as-is.
        """
        if image is None or not image.startswith("urn:"):
            return image

        # The same element may exist in multiple repositories, so the URN
        # can match several artifacts. Pick the one from the highest-priority
        # repository (Repository.priority: higher value = more priority).
        artifacts = repo_models.RepoArtifact.objects.get_all(
            filters={"urn": dm_filters.EQ(image)}
        )
        if not artifacts:
            raise ValueError(f"Artifact for URN '{image}' not found")

        if len(artifacts) == 1:
            return artifacts[0].uri

        artifact = max(artifacts, key=lambda a: a.element.repository.priority)
        return artifact.uri

    def _actualize_machine_derivatives_on_create_update(
        self,
        machine: pool_models.Machine,
        machine_pool_pair: (
            tp.Optional[
                tp.Tuple[pool_models.PoolMachine, tp.Optional[pool_models.PoolMachine]]
            ]
        ) = None,
        machine_guest_pair: (
            tp.Optional[
                tp.Tuple[
                    pool_models.GuestMachine, tp.Optional[pool_models.GuestMachine]
                ]
            ]
        ) = None,
    ) -> tp.Collection[pool_models.PoolMachine | pool_models.GuestMachine]:
        """Actualize the machine derivatives."""
        port, volume = self._get_or_fetch_machine_ctx(machine)

        if port is None or volume is None:
            raise ValueError(f"Machine {machine.uuid} has no port or volume")

        # Resolve image URN to URI if the image is specified as a URN
        resolved_image = self._resolve_image(volume.image)

        # Save guest agent to speed up scheduling process
        guest_agent = ua_models.UniversalAgent.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(machine.uuid)}
        )
        if guest_agent is None:
            guest_agent = ua_models.UniversalAgent(
                uuid=machine.uuid,
                node=machine.node.uuid,
                name="dummy-node-agent",
            )
            guest_agent.save()

        # The new machine and the machine with changed image should be booted
        # in the private network with boot=`network`.
        boot = nc.BootAlternative.hd0.value
        if machine_guest_pair is None:
            # Do nothing for new machines in the core set.
            # The core machines start in boot=hd0 mode and need to be
            # attached to the main network.
            if not self._is_core_machine(machine):
                # It's a new machine, so it should be run in the
                # `network` boot mode.
                boot = nc.BootAlternative.network.value
                # Any port for the boot network is fine. The port will be replaced
                # after the machine is flashed and switched to the main network.
                port = models.Port.from_boot_network()
        else:
            # Don't swith boot mode for the core set as the update procedure
            # is performed by guest machine driver.
            if not self._is_core_machine(machine):
                _, guest_actual = machine_guest_pair
                # The image is changed, so the machine should be booted in the
                # `network` boot mode and flashed with a new image.
                if guest_actual and guest_actual.image != resolved_image:
                    boot = nc.BootAlternative.network.value
                    # Any port for the boot network is fine. The port will be replaced
                    # after the machine is flashed and switched to the main network.
                    port = models.Port.from_boot_network()

        # Set correct boot value for the machine (root model)
        machine.boot = boot
        machine.image = volume.image

        # Find the agent UUID for the pool
        agent_uuid = self._agent_by_pool(machine.pool.uuid)

        # Pool machine
        pool_machine = pool_models.PoolMachine.from_machine_and_port(
            machine, port, agent_uuid=agent_uuid
        )

        # Guest machine
        guest = pool_models.GuestMachine(
            uuid=machine.uuid,
            image=resolved_image,
            agent_uuid=guest_agent.uuid,
            hostname=machine.node.hostname or machine.node.name,
            boot=boot,
        )

        return (pool_machine, guest)

    def _actualize_machine_derivatives_on_outdate(
        self,
        machine: pool_models.Machine,
        derivative_pairs: tp.Collection[
            tp.Tuple[
                ua_models.TargetResourceKindAwareMixin,  # The target resource
                tp.Optional[
                    ua_models.TargetResourceKindAwareMixin
                ],  # The actual resource
            ]
        ],
    ) -> tp.Collection[ua_models.TargetResourceKindAwareMixin]:
        """Actualize outdated machine with derivatives."""
        target_guest_machine, guest_machine = None, None
        target_pool_machine, pool_machine = None, None

        # Find the guest and pool machines
        for pair in derivative_pairs:
            if isinstance(pair[0], pool_models.GuestMachine):
                target_guest_machine, guest_machine = pair
            elif isinstance(pair[0], pool_models.PoolMachine):
                target_pool_machine, pool_machine = pair

        if not target_pool_machine or not target_guest_machine:
            raise ValueError("Target pool machine or guest machine is missing")

        # The machine has been flashed. Switch to the HD boot
        # and the main network
        if (
            guest_machine is not None
            and guest_machine.boot == nc.BootAlternative.network
            and guest_machine.status == nc.MachineStatus.FLASHED
        ):
            # Get the port from the main network for the machine
            port, _ = self._get_or_fetch_machine_ctx(machine)
            if port is None:
                raise ValueError(f"Machine {machine.uuid} has no port")

            # FIXME(akremenetsky): Detect disk number
            boot = nc.BootAlternative.hd0.value

            # Find the agent UUID for the pool
            agent_uuid = self._agent_by_pool(machine.pool.uuid)
            machine.boot = boot
            target_guest_machine.boot = boot

            # Rebuid the pool machine with correct boot mode and the port
            # from the main network
            target_pool_machine = pool_models.PoolMachine.from_machine_and_port(
                machine, port, agent_uuid=agent_uuid
            )

        # Actualize status
        self._actualize_machine_status(
            machine,
            pool_machine,
            guest_machine,
        )

        return target_pool_machine, target_guest_machine

    def _actualize_machine_status(
        self,
        machine: pool_models.Machine,
        pool_machine: tp.Optional[pool_models.PoolMachine],
        guest_machine: tp.Optional[pool_models.GuestMachine],
    ) -> None:
        """Actualize the machine status."""

        if pool_machine is None or guest_machine is None:
            return

        if (
            pool_machine.status == nc.MachineStatus.ERROR
            or guest_machine.status == nc.MachineStatus.ERROR
        ):
            machine.status = nc.MachineStatus.ERROR.value
            return

        if (
            pool_machine.status == nc.MachineStatus.NEW
            or guest_machine.status == nc.MachineStatus.NEW
        ):
            machine.status = nc.MachineStatus.NEW.value
            return

        if (
            pool_machine.status == nc.MachineStatus.ACTIVE
            and guest_machine.status == nc.MachineStatus.ACTIVE
        ):
            # Transition to ACTIVE only when the machine images are actual.
            # Without this check the machine could be marked ACTIVE while
            # pool_machine or guest_machine still use stale images from a
            # previous version.
            resolved_image = self._resolve_image(machine.image)
            if (
                machine.image == pool_machine.image
                and resolved_image == guest_machine.image
            ):
                machine.status = nc.MachineStatus.ACTIVE.value
                return

        # TODO(akremenetsky): Support more statuses
        machine.status = nc.MachineStatus.IN_PROGRESS.value

    def _pre_delete_machine_resource(self, resource: ua_models.TargetResource) -> None:
        """The hook is performed before deleting machine resource."""
        # Clear all resources placed on the node
        resources = ua_models.Resource.objects.get_all(
            filters={
                "node": dm_filters.EQ(resource.uuid),
            }
        )

        # Clear all agents run on the node
        agents = ua_models.UniversalAgent.objects.get_all(
            filters={
                "node": dm_filters.EQ(resource.uuid),
            }
        )

        # Rescheduler target resource
        if agents:
            target_resources = ua_models.TargetResource.objects.get_all(
                filters={
                    "agent": dm_filters.In((a.uuid for a in agents)),
                }
            )

            for target_resource in target_resources:
                target_resource.agent = None
                target_resource.save()

        # Clear all resources and agents
        for obj in resources + agents:
            obj.delete()

    # Volume

    def _has_enough_space_in_pool(
        self,
        pool: pool_models.Pool,
        target_volume: pool_models.MachineVolume,
        actual_volume: tp.Optional[pool_models.MachineVolume] = None,
    ) -> bool:
        """Check if the pool has enough resources to create the machine."""
        # Calculate how many resources we need to create the machine
        size = actual_volume.size if actual_volume is not None else 0
        size = target_volume.size - size

        if not pool.storage_pools:
            return False

        storage_pool: models.AbstractStoragePool = pool.storage_pools[0]
        return storage_pool.has_capacity(size)

    def _reschedule_volume(
        self,
        volume: pool_models.MachineVolume,
    ) -> None:
        """Reschedule the volume."""
        # The scheduler will try to schedule the volume again
        volume.delete()

    def _can_create_volume(
        self,
        volume: pool_models.MachineVolume,
    ) -> bool:
        """Check if the volume can be created."""

        # Check pool has enough space
        if not self._has_enough_space_in_pool(volume.pool, volume):
            LOG.warning("Pool %s has not enough space", volume.pool)

            # Seems the scheduler missed the pool. Try to reschedule
            # the volume and machine if it's the root volume.
            if volume.index == 0 and volume.machine:
                self._reschedule_volume(volume)
                self._reschedule_machine(volume.machine)
                return False

            self._reschedule_volume(volume)
            return False

        storage_pool: models.AbstractStoragePool = volume.pool.storage_pools[0]

        # FIXME(akremenetsky): Does it work correctly?
        # Will every volume refer to own pool object?
        storage_pool.allocate_capacity(volume.size)

        return True

    def _can_update_volume(
        self,
        volume: pool_models.MachineVolume,
        resource: ua_models.TargetResource,
    ) -> bool:
        """Check if the volume can be updated."""
        target_volume = volume
        actual_volume = pool_models.MachineVolume.from_ua_resource(resource)

        # Check pool has enough space
        if not self._has_enough_space_in_pool(
            volume.pool, target_volume, actual_volume
        ):
            LOG.warning("Pool %s has not enough space", volume.pool)

            # FIXME(akremenetsky): It's more safer do nothing and set the
            # volume status to ERROR than to try reschedule it.
            volume.status = nc.VolumeStatus.ERROR.value
            volume.node_volume.status = nc.VolumeStatus.ERROR.value
            volume.save()
            volume.node_volume.save()
            return False

        storage_pool: models.AbstractStoragePool = volume.pool.storage_pools[0]

        # FIXME(akremenetsky): Does it work correctly?
        # Will every volume refer to own pool object?
        storage_pool.allocate_capacity(target_volume.size - actual_volume.size)

        return True

    # Builder lifecycle hooks

    def prepare_iteration(self) -> tp.Dict[str, tp.Any]:
        """Perform actions before iteration and return the iteration context.

        The result is a dictionary that is passed to the iteration context.
        """
        pools = models.MachinePool.objects.get_all(
            filters={
                "builder": dm_filters.EQ(self.ua_service_spec.uuid),
            },
        )

        return {
            "clause_filters": {
                "builder": self.ua_service_spec.uuid,
                "pools": pools,
            }
        }

    @functools.singledispatchmethod
    def can_create_instance_resource(
        self,
        instance: (pool_models.Machine | pool_models.MachineVolume | pool_models.Pool),
    ) -> bool:
        """The hook to check if the instance can be created.

        If the hook returns `False`, the code related to the instance:
        - `pre_create_instance_resource`
        - `create_instance_derivatives`
        - `post_create_instance_resource`
        will be skipped for the current iteration. The
        `can_create_instance_resource` will be called again on the next
        iteration until it returns `True`.
        """
        raise TypeError(f"Unsupported type: {type(instance)}")

    @can_create_instance_resource.register
    def _(
        self,
        instance: pool_models.Machine,
    ) -> bool:
        return self._can_create_machine(instance)

    @can_create_instance_resource.register
    def _(
        self,
        instance: pool_models.MachineVolume,
    ) -> bool:
        return self._can_create_volume(instance)

    @can_create_instance_resource.register
    def _(
        self,
        instance: pool_models.Pool,
    ) -> bool:
        return True

    def create_instance_derivatives(
        self,
        instance: pool_models.Machine,
    ) -> tp.Collection[pool_models.PoolMachine | pool_models.GuestMachine]:
        """Create the instance.

        The result is a collection of derivative objects that are
        required for the instance. For example, the main instance is a
        `Config` so the derivative objects for the the config is a list
        of `Render`. The result is a tuple/list/set/... of render objects.
        The derivative objects should inherit from the `TargetResourceMixin`.

        The hook is called only for new instances.
        """
        return self._actualize_machine_derivatives_on_create_update(instance)

    def update_instance_derivatives(
        self,
        instance: pool_models.Machine,
        resource: ua_models.TargetResource,
        derivative_pairs: tp.Collection[
            tp.Tuple[
                ua_models.TargetResourceKindAwareMixin,  # The target resource
                tp.Optional[
                    ua_models.TargetResourceKindAwareMixin
                ],  # The actual resource
            ]
        ],
    ) -> tp.Collection[pool_models.PoolMachine | pool_models.GuestMachine]:
        """The hook to update instance derivatives.

        The hook is called when an initiator of updating is an user or
        software from control plane side.
        The default behavior is to send the same list as on instance creation.
        """
        machine_pool_pair = None
        machine_guest_pair = None

        for pair in derivative_pairs:
            if isinstance(pair[0], pool_models.PoolMachine):
                machine_pool_pair = pair
            elif isinstance(pair[0], pool_models.GuestMachine):
                machine_guest_pair = pair

        return self._actualize_machine_derivatives_on_create_update(
            instance, machine_pool_pair, machine_guest_pair
        )

    @functools.singledispatchmethod
    def can_update_instance_resource(
        self,
        instance: (pool_models.Machine | pool_models.MachineVolume | pool_models.Pool),
        resource: ua_models.TargetResource,
    ) -> bool:
        """The hook to check if the instance can be updated.

        If the hook returns `False`, the code related to the instance:
        - `update_instance_derivatives`
        - `post_update_instance_resource`
        will be skipped for the current iteration. The
        `can_update_instance_resource` will be called again on the next
        iteration until it returns `True`.
        """
        raise TypeError(f"Unsupported type: {type(instance)}")

    @can_update_instance_resource.register
    def _(
        self,
        instance: pool_models.Machine,
        resource: ua_models.TargetResource,
    ) -> bool:
        return self._can_update_machine(instance, resource)

    @can_update_instance_resource.register
    def _(
        self,
        instance: pool_models.MachineVolume,
        resource: ua_models.TargetResource,
    ) -> bool:
        return self._can_update_volume(instance, resource)

    @can_update_instance_resource.register
    def _(
        self,
        instance: pool_models.Pool,
        resource: ua_models.TargetResource,
    ) -> bool:
        return True

    @functools.singledispatchmethod
    def actualize_outdated_instance(
        self,
        current_instance: pool_models.Pool | pool_models.MachineVolume,
        actual_instance: pool_models.Pool | pool_models.MachineVolume,
    ) -> None:
        """Actualize outdated instance.

        It means some changes occurred on the data plane and the instance
        is outdated now. For example, the instance `Password` has field
        `value` that is stored in the secret storage. If the value is changed
        or created on the data plane, the instance is outdated and this method
        is called to reactualize the instance.

        Args:
            current_instance: The current instance.
            actual_instance: The actual instance.
        """
        raise TypeError(f"Unsupported type: {type(current_instance)}")

    @actualize_outdated_instance.register
    def _(
        self,
        current_instance: pool_models.Pool,
        actual_instance: pool_models.Pool,
    ) -> None:
        current_instance.all_cores = actual_instance.all_cores
        current_instance.all_ram = actual_instance.all_ram
        current_instance.avail_cores = actual_instance.avail_cores
        current_instance.avail_ram = actual_instance.avail_ram
        current_instance.storage_pools = actual_instance.storage_pools
        current_instance.status = actual_instance.status

    @actualize_outdated_instance.register
    def _(
        self,
        current_instance: pool_models.MachineVolume,
        actual_instance: pool_models.MachineVolume,
    ) -> None:
        current_instance.status = actual_instance.status

    def actualize_outdated_instance_derivatives(
        self,
        instance: pool_models.Machine,
        derivative_pairs: tp.Collection[
            tp.Tuple[
                ua_models.TargetResourceKindAwareMixin,  # The target resource
                tp.Optional[
                    ua_models.TargetResourceKindAwareMixin
                ],  # The actual resource
            ]
        ],
    ) -> tp.Collection[ua_models.TargetResourceKindAwareMixin]:
        """Actualize outdated machine with derivatives."""
        return self._actualize_machine_derivatives_on_outdate(
            instance, derivative_pairs
        )

    def pre_delete_instance_resource(self, resource: ua_models.TargetResource) -> None:
        """The hook is performed before deleting instance resource."""
        if resource.kind == "machine":
            return self._pre_delete_machine_resource(resource)
