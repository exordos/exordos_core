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

import typing as tp
import uuid as sys_uuid

from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.agents.universal.drivers import pool as ua_pool
from gcl_sdk.agents.universal.services import builder as sdk_builder
from restalchemy.dm import filters as dm_filters
from restalchemy.dm import models as ra_models
from restalchemy.dm import properties
from restalchemy.dm import relationships
from restalchemy.dm import types
from restalchemy.dm import types_network

from exordos_core.compute.dm import models


class SchedulableToAgentFromAgentFieldMixin(ua_models.SchedulableToAgentMixin):
    def schedule_to_ua_agent(self, **kwargs) -> tp.Optional[sys_uuid.UUID]:
        """Schedule the resource to the UA agent.

        The method returns the node UUID that is equal to the
        agent UUID.
        """
        return self.agent


class SchedulableToAgentFromPoolMixin(ua_models.SchedulableToAgentMixin):
    def schedule_to_ua_agent(
        self, builder: sdk_builder.UniversalBuilderService, **kwargs
    ) -> tp.Optional[sys_uuid.UUID]:
        """Schedule the resource to the UA agent.

        The method returns the node UUID that is equal to the
        agent UUID.
        """
        # Apply scheduing info to the target resource
        if instance_pool := self.pool:
            if isinstance(instance_pool, Pool):
                instance_pool = instance_pool.uuid

            # TODO(akremenetsky): Interface for `_iteration_context`
            for pool in builder._iteration_context["clause_filters"]["pools"]:
                if pool.uuid == instance_pool:
                    return pool.agent

        raise ValueError(f"Pool {self.pool} not found")


class Pool(
    models.MachinePool,
    ua_models.InstanceMixin,
    SchedulableToAgentFromAgentFieldMixin,
):
    @classmethod
    def get_resource_kind(cls) -> str:
        return "pool"

    @classmethod
    def get_filter_clause(
        cls, builder: sys_uuid.UUID, pools: tp.List["Pool"]
    ) -> tp.Optional[tp.Dict[str, dm_filters.AbstractClause]]:
        """Get filter clause for the instance model.

        The clause is returned back to the service to take a chance for
        the service enrich the clause. After that the clause is used in
        the database queries. The service haven't must call method
        `get_new_instances` and other with the clause if it was returned.
        It depends on the service implementation.
        """
        return {"builder": dm_filters.EQ(str(builder))}

    def get_resource_target_fields(self) -> tp.Collection[str]:
        """Return the collection of target fields.

        Refer to the Resource model for more details about target fields.
        """
        return frozenset(
            ("uuid", "driver_spec", "machine_type", "cores_ratio", "ram_ratio")
        )


class MachineVolume(
    models.MachineVolume,
    ua_models.InstanceMixin,
    SchedulableToAgentFromPoolMixin,
):
    pool = relationships.relationship(Pool, prefetch=True)
    machine = relationships.relationship(models.Machine, prefetch=True)

    @classmethod
    def get_resource_kind(cls) -> str:
        return "pool_volume"

    @classmethod
    def get_filter_clause(
        cls, builder: sys_uuid.UUID, pools: tp.List[Pool]
    ) -> tp.Optional[tp.Dict[str, dm_filters.AbstractClause]]:
        """Get filter clause for the instance model.

        The clause is returned back to the service to take a chance for
        the service enrich the clause. After that the clause is used in
        the database queries. The service haven't must call method
        `get_new_instances` and other with the clause if it was returned.
        It depends on the service implementation.
        """
        return {"pool": dm_filters.In([p.uuid for p in pools])}

    def get_resource_target_fields(self) -> tp.Collection[str]:
        """Return the collection of target fields.

        Refer to the Resource model for more details about target fields.
        """
        return frozenset(
            (
                "uuid",
                "pool",
                "name",
                "index",
                "size",
                "image",
                "machine",
                "boot",
                "label",
                "device_type",
                "project_id",
            )
        )


class PoolMachine(
    ua_models.SchedulableToAgentFromAgentUUIDMixin,
    ua_models.TargetResourceKindAwareMixin,
    ra_models.ModelWithUUID,
):
    name = properties.property(types.String(max_length=255), default="")
    cores = properties.property(types.Integer(min_value=0, max_value=4096), default=0)
    ram = properties.property(types.Integer(min_value=0), default=0)
    status = properties.property(
        types.Enum([s.value for s in ua_pool.MachineStatus]),
        default=ua_pool.MachineStatus.NEW.value,
    )
    machine_type = properties.property(
        types.Enum([t.value for t in ua_pool.NodeType]),
        default=ua_pool.NodeType.VM.value,
    )
    node = properties.property(types.AllowNone(types.UUID()), default=None)
    pool = properties.property(types.UUID(), required=True)
    boot = properties.property(
        types.Enum([b.value for b in ua_pool.BootAlternative]),
        default=ua_pool.BootAlternative.network.value,
    )
    image = properties.property(
        types.AllowNone(types.String(max_length=255)), default=None
    )

    project_id = properties.property(types.UUID(), required=True, read_only=True)

    port_info = properties.property(types.Dict(), default=dict)

    @classmethod
    def get_resource_kind(cls) -> str:
        """Return the resource kind."""
        return "pool_machine"

    def get_resource_target_fields(self) -> tp.Collection[str]:
        """Return the collection of target fields.

        Refer to the Resource model for more details about target fields.
        """
        return frozenset(
            (
                "uuid",
                "name",
                "project_id",
                "cores",
                "ram",
                "machine_type",
                "node",
                "boot",
                "pool",
                "port_info",
                "image",
            )
        )

    @classmethod
    def from_machine_and_port(
        cls,
        machine: "Machine",
        port: models.Port,
        agent_uuid: tp.Optional[str] = None,
    ) -> "PoolMachine":
        return cls(
            uuid=machine.uuid,
            name=machine.name,
            project_id=machine.project_id,
            cores=machine.cores,
            ram=machine.ram,
            image=machine.image,
            machine_type=machine.machine_type,
            boot=machine.boot,
            node=machine.node.uuid,
            pool=machine.pool.uuid,
            agent_uuid=agent_uuid,
            port_info={
                "ipv4": str(port.ipv4) if port.ipv4 else None,
                "mask": str(port.mask) if port.mask else None,
                "mac": port.mac,
                "source": port.source,
            },
        )


class GuestMachine(
    ua_models.TargetResourceKindAwareMixin,
    ua_models.SchedulableToAgentFromAgentUUIDMixin,
    ra_models.ModelWithUUID,
):
    """The model represents a guest machine."""

    image = properties.property(types.String(), required=True)
    hostname = properties.property(
        types.AllowNone(types_network.Hostname()), default=None
    )
    boot = properties.property(
        types.Enum([b.value for b in ua_pool.BootAlternative]),
        default=ua_pool.BootAlternative.network.value,
    )
    block_devices = properties.property(types.Dict(), default=dict)
    net_devices = properties.property(types.Dict(), default=dict)
    pci_devices = properties.property(types.Dict(), default=dict)

    status = properties.property(
        types.Enum([s.value for s in ua_pool.MachineStatus]),
        default=ua_pool.MachineStatus.NEW.value,
    )

    @classmethod
    def get_resource_kind(cls) -> str:
        """Return the resource kind."""
        return "guest_machine"

    def get_resource_target_fields(self) -> tp.Collection[str]:
        """Return the collection of target fields.

        Refer to the Resource model for more details about target fields.
        """
        return frozenset(("uuid", "image", "hostname", "boot"))


class Machine(
    models.Machine,
    ua_models.InstanceWithDerivativesMixin,
):
    __derivative_model_map__ = {
        "pool_machine": PoolMachine,
        "guest_machine": GuestMachine,
    }

    pool = relationships.relationship(Pool, prefetch=True)
    node = relationships.relationship(models.Node, prefetch=True)

    @classmethod
    def get_resource_kind(cls) -> str:
        return "machine"

    @classmethod
    def get_filter_clause(
        cls, builder: sys_uuid.UUID, pools: tp.List[Pool]
    ) -> tp.Optional[tp.Dict[str, dm_filters.AbstractClause]]:
        """Get filter clause for the instance model.

        The clause is returned back to the service to take a chance for
        the service enrich the clause. After that the clause is used in
        the database queries. The service haven't must call method
        `get_new_instances` and other with the clause if it was returned.
        It depends on the service implementation.
        """
        return {"pool": dm_filters.In([p.uuid for p in pools])}
