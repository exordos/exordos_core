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

from types import SimpleNamespace
from unittest import mock
import uuid as sys_uuid

from gcl_sdk.agents.universal.drivers import pool as ua_pool
from gcl_sdk.infra import constants as ic
from gcl_sdk.infra.dm import models as infra_models
from gcl_sdk.infra.services import builder as infra_builder
import pytest
from restalchemy.storage.sql import orm

from exordos_core.compute import constants as nc
from exordos_core.compute.builders import node as node_builder
from exordos_core.compute.builders import node_set as node_set_builder
from exordos_core.compute.builders import pool as pool_builder
from exordos_core.compute.dm import models as compute_models
from exordos_core.compute.node_set.dm import models as set_models

PS = ic.PowerState


def _node(**kwargs):
    node = mock.MagicMock()
    node.uuid = sys_uuid.uuid4()
    node.cores = 1
    node.ram = 512
    node.name = "node"
    node.description = ""
    node.hostname = "node"
    node.status = nc.NodeStatus.IN_PROGRESS.value
    node.target_power_state = PS.ON.value
    for key, value in kwargs.items():
        setattr(node, key, value)
    return node


class TestNodeBuilderPowerState:
    @pytest.fixture
    def machine(self, monkeypatch):
        machine = mock.MagicMock()
        machine.cores = 1
        machine.ram = 512
        machine.name = "node"
        machine.description = ""
        machine.target_power_state = PS.ON.value

        def fake_get_one_or_none(self, *args, **kwargs):
            if self.model_cls is compute_models.Machine:
                return machine
            raise NotImplementedError

        monkeypatch.setattr(
            orm.ObjectCollection, "get_one_or_none", fake_get_one_or_none
        )
        return machine

    @pytest.mark.parametrize("target", [PS.OFF.value, PS.REBOOT.value])
    def test_target_power_state_passed_to_machine(self, machine, target):
        service = node_builder.NodeBuilderService()

        service._update_machine(
            _node(target_power_state=target),
            _node(target_power_state=PS.ON.value),
        )

        assert machine.target_power_state == target
        assert machine.status == ua_pool.MachineStatus.IN_PROGRESS.value
        machine.update.assert_called_once()

    def test_new_reboot_with_stale_node_resource(self, machine):
        # The previous reboot switched the target back to `ON` bypassing
        # the node resource, so the resource keeps `REBOOT`.
        service = node_builder.NodeBuilderService()

        service._update_machine(
            _node(target_power_state=PS.REBOOT.value),
            _node(target_power_state=PS.REBOOT.value),
        )

        assert machine.target_power_state == PS.REBOOT.value
        machine.update.assert_called_once()

    def test_reboot_already_passed_to_machine(self, machine):
        service = node_builder.NodeBuilderService()
        machine.target_power_state = PS.REBOOT.value

        service._update_machine(
            _node(target_power_state=PS.REBOOT.value),
            _node(target_power_state=PS.REBOOT.value),
        )

        machine.update.assert_not_called()

    def test_status_from_machine_when_nothing_changed(self, machine):
        # The power state was switched and switched back by the API
        # before the node builder saw it
        service = node_builder.NodeBuilderService()
        machine.target_power_state = PS.OFF.value
        machine.status = ua_pool.MachineStatus.ACTIVE.value
        node = _node(target_power_state=PS.OFF.value)

        service._update_machine(node, _node(target_power_state=PS.OFF.value))

        assert node.status == ua_pool.MachineStatus.ACTIVE.value
        machine.update.assert_not_called()

    def test_actual_power_state_from_machine(self):
        service = node_builder.NodeBuilderService()
        node = _node(target_power_state=PS.OFF.value, node_set=None)
        machine = mock.MagicMock(
            status=ua_pool.MachineStatus.ACTIVE.value,
            power_state=PS.OFF.value,
        )

        service.actualize_instance_with_outdated_tracked(node, [machine])

        assert node.status == ua_pool.MachineStatus.ACTIVE.value
        assert node.power_state == PS.OFF.value
        machine.update.assert_not_called()

    def test_rebooted_node_switched_to_on(self):
        service = node_builder.NodeBuilderService()
        node = _node(target_power_state=PS.REBOOT.value, node_set=None)
        machine = mock.MagicMock(
            status=ua_pool.MachineStatus.IN_PROGRESS.value,
            power_state=PS.REBOOTED.value,
            target_power_state=PS.REBOOT.value,
        )

        service.actualize_instance_with_outdated_tracked(node, [machine])

        assert node.power_state == PS.REBOOTED.value
        assert node.target_power_state == PS.ON.value
        assert node.status == nc.NodeStatus.IN_PROGRESS.value
        assert machine.target_power_state == PS.ON.value
        assert machine.status == ua_pool.MachineStatus.IN_PROGRESS.value
        machine.update.assert_called_once()

    def test_rebooted_set_node_waits_for_set(self):
        service = node_builder.NodeBuilderService()
        node = _node(target_power_state=PS.REBOOT.value, node_set=sys_uuid.uuid4())
        machine = mock.MagicMock(
            status=ua_pool.MachineStatus.IN_PROGRESS.value,
            power_state=PS.REBOOTED.value,
            target_power_state=PS.REBOOT.value,
        )

        service.actualize_instance_with_outdated_tracked(node, [machine])

        assert node.power_state == PS.REBOOTED.value
        assert node.target_power_state == PS.REBOOT.value
        assert machine.target_power_state == PS.REBOOT.value
        machine.update.assert_not_called()


class TestPoolBuilderPowerState:
    @staticmethod
    def _actualize(target, actual):
        service = pool_builder.PoolBuilderService(sys_uuid.uuid4(), mock.Mock())
        machine = SimpleNamespace(
            image="image",
            target_power_state=target,
            power_state=PS.ON.value,
            status=ua_pool.MachineStatus.IN_PROGRESS.value,
        )
        pool_machine = SimpleNamespace(
            status=ua_pool.MachineStatus.ACTIVE.value,
            image="image",
            power_state=actual,
        )
        guest_machine = SimpleNamespace(
            status=ua_pool.MachineStatus.ACTIVE.value,
            image="image",
        )

        service._actualize_machine_status(machine, pool_machine, guest_machine)
        return machine

    @pytest.mark.parametrize("state", [PS.ON.value, PS.OFF.value])
    def test_active_when_power_state_reached(self, state):
        machine = self._actualize(state, state)

        assert machine.status == ua_pool.MachineStatus.ACTIVE.value
        assert machine.power_state == state

    @pytest.mark.parametrize(
        "target,actual",
        [
            (PS.OFF.value, PS.ON.value),
            (PS.ON.value, PS.OFF.value),
            (PS.ON.value, PS.UNKNOWN.value),
            (PS.REBOOT.value, PS.ON.value),
            (PS.REBOOT.value, PS.REBOOTING.value),
            (PS.REBOOT.value, PS.REBOOTED.value),
            # The reboot is done but the agent hasn't got the new target
            (PS.ON.value, PS.REBOOTED.value),
        ],
    )
    def test_in_progress_until_power_state_reached(self, target, actual):
        machine = self._actualize(target, actual)

        assert machine.status == ua_pool.MachineStatus.IN_PROGRESS.value
        assert machine.power_state == actual

    def test_pool_machine_gets_target_power_state(self):
        machine = SimpleNamespace(
            uuid=sys_uuid.uuid4(),
            name="node",
            project_id=sys_uuid.uuid4(),
            cores=1,
            ram=512,
            image=None,
            machine_type=ua_pool.NodeType.VM.value,
            boot=ua_pool.BootAlternative.hd0.value,
            node=SimpleNamespace(uuid=sys_uuid.uuid4()),
            pool=SimpleNamespace(uuid=sys_uuid.uuid4()),
            target_power_state=PS.REBOOT.value,
        )
        port = SimpleNamespace(ipv4=None, mask=None, mac=None, source=None)

        pool_machine = pool_builder.pool_models.PoolMachine.from_machine_and_port(
            machine, port
        )

        assert pool_machine.power_state == PS.REBOOT.value
        assert "power_state" in pool_machine.get_resource_target_fields()


class TestNodeSetBuilderPowerState:
    @pytest.fixture
    def service(self):
        service = node_set_builder.NodeSetBuilderService(
            set_models.NodeSet, sys_uuid.uuid4()
        )
        policy = SimpleNamespace(uuid=sys_uuid.uuid4())
        with mock.patch.object(
            service, "_get_or_create_placement_policy", return_value=policy
        ):
            yield service

    @staticmethod
    def _node_set(target):
        return set_models.NodeSet(
            name="set",
            project_id=sys_uuid.uuid4(),
            cores=1,
            ram=512,
            replicas=2,
            disk_spec=infra_models.SetRootDiskSpec(image="ubuntu_24.04"),
            target_power_state=target,
        )

    @staticmethod
    def _infra(node_set, actual_states):
        pairs = []
        for target in node_set.gen_nodes(sys_uuid.uuid4()):
            actual = None
            state = actual_states.pop(0)
            if state is not None:
                actual = set_models.Node.restore_from_simple_view(
                    **target.dump_to_simple_view()
                )
                actual.power_state = state
            pairs.append(infra_builder.InfraResourcePair(target, actual))
        return infra_builder.InfraCollection(infra_objects=tuple(pairs))

    def test_nodes_get_target_power_state(self, service):
        node_set = self._node_set(PS.OFF.value)
        infra = self._infra(node_set, [PS.ON.value, PS.ON.value])

        nodes = service.actualize_infra(node_set, infra)

        nodes = [n for n in nodes if isinstance(n, set_models.Node)]
        assert len(nodes) == 2
        assert all(n.target_power_state == PS.OFF.value for n in nodes)

    @pytest.mark.parametrize(
        "states",
        [
            [PS.REBOOTED.value, PS.REBOOTING.value],
            [PS.REBOOTED.value, PS.ON.value],
            [PS.REBOOTED.value, None],
        ],
    )
    def test_reboot_waits_for_all_nodes(self, service, states):
        node_set = self._node_set(PS.REBOOT.value)
        infra = self._infra(node_set, states)

        nodes = service.actualize_infra(node_set, infra)

        assert node_set.target_power_state == PS.REBOOT.value
        nodes = [n for n in nodes if isinstance(n, set_models.Node)]
        assert all(n.target_power_state == PS.REBOOT.value for n in nodes)

    def test_all_nodes_rebooted(self, service):
        node_set = self._node_set(PS.REBOOT.value)
        infra = self._infra(node_set, [PS.REBOOTED.value, PS.REBOOTED.value])

        nodes = service.actualize_infra(node_set, infra)

        assert node_set.target_power_state == PS.ON.value
        nodes = [n for n in nodes if isinstance(n, set_models.Node)]
        assert len(nodes) == 2
        assert all(n.target_power_state == PS.ON.value for n in nodes)
