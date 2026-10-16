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

from unittest import mock
import uuid as sys_uuid

from gcl_sdk.agents.universal.drivers import pool as ua_pool
from gcl_sdk.infra.dm import models as sdk_models
import pytest
from restalchemy.storage.sql import orm

from exordos_core.compute import constants as nc
from exordos_core.compute.builders import node as node_builder
from exordos_core.compute.dm import models as compute_models


class TestNodeBuilderService:
    @pytest.mark.parametrize("status", ["ACTIVE", "IN_PROGRESS", "ERROR", None])
    def test_network_update_restores_current_machine_status(self, status):
        service = node_builder.NodeBuilderService()
        node = node_builder.Node(
            name="guest",
            project_id=sys_uuid.uuid4(),
            cores=1,
            ram=1024,
            disk_spec=sdk_models.DisksSpec(disks=[]),
        )
        resource = node.to_ua_resource()
        node.interfaces = []
        node.status = nc.NodeStatus.IN_PROGRESS.value
        machine = mock.MagicMock(status=status) if status is not None else None

        with mock.patch.object(
            orm.ObjectCollection, "get_one_or_none", return_value=machine
        ):
            service.pre_update_instance_resource(node, resource)

        assert node.status == (status or nc.NodeStatus.IN_PROGRESS.value)
        if machine is not None:
            machine.update.assert_not_called()

    def test_compute_update_waits_for_machine_convergence(self):
        service = node_builder.NodeBuilderService()
        node = node_builder.Node(
            name="guest",
            project_id=sys_uuid.uuid4(),
            cores=1,
            ram=1024,
            disk_spec=sdk_models.DisksSpec(disks=[]),
        )
        resource = node.to_ua_resource()
        node.cores = 2
        node.status = nc.NodeStatus.IN_PROGRESS.value
        machine = mock.MagicMock(cores=1, ram=1024, description="", status="ACTIVE")
        machine.name = "guest"

        with mock.patch.object(
            orm.ObjectCollection, "get_one_or_none", return_value=machine
        ):
            service.pre_update_instance_resource(node, resource)

        assert node.status == nc.NodeStatus.IN_PROGRESS.value
        assert machine.status == ua_pool.MachineStatus.IN_PROGRESS.value
        assert machine.cores == 2
        machine.update.assert_called_once_with(force=False)

    def test_update_machine_sets_node_status_to_in_progress(
        self,
        monkeypatch,
    ):
        service = node_builder.NodeBuilderService()
        node_uuid = sys_uuid.uuid4()

        target_node = mock.MagicMock()
        target_node.uuid = node_uuid
        target_node.cores = 2
        target_node.ram = 1024
        target_node.name = "target-name"
        target_node.description = "target-desc"
        target_node.hostname = "host-a"
        target_node.status = nc.NodeStatus.ACTIVE.value

        actual_node = mock.MagicMock()
        actual_node.uuid = node_uuid
        actual_node.cores = 4
        actual_node.ram = 2048
        actual_node.name = "actual-name"
        actual_node.description = "actual-desc"
        actual_node.hostname = "host-b"

        machine = mock.MagicMock()
        machine.cores = 1
        machine.ram = 512
        machine.name = "old-name"
        machine.description = "old-desc"

        def fake_get_one_or_none(self, *args, **kwargs):
            if self.model_cls is compute_models.Machine:
                return machine
            raise NotImplementedError

        monkeypatch.setattr(
            orm.ObjectCollection, "get_one_or_none", fake_get_one_or_none
        )

        service._update_machine(target_node, actual_node)

        assert machine.cores == target_node.cores
        assert machine.ram == target_node.ram
        assert machine.name == target_node.name
        assert machine.description == target_node.description
        assert machine.status == ua_pool.MachineStatus.IN_PROGRESS.value
        machine.update.assert_called_once_with(force=True)
        assert target_node.status == nc.NodeStatus.IN_PROGRESS.value
        target_node.save.assert_called_once_with()

    def test_resize_preserves_immutable_disk_policy(self):
        service = node_builder.NodeBuilderService()
        project = sys_uuid.uuid4()
        target = compute_models.Volume(size=20, project_id=project)
        actual = compute_models.Volume(size=10, project_id=project)
        with mock.patch.object(actual, "save"):
            service._actualize_volume(target, actual)
        assert actual.size == 20
        assert actual.tier == target.tier
        assert actual.ephemeral == target.ephemeral

    def test_disk_policy_change_is_rejected_before_mutating_disk(self):
        import pytest

        service = node_builder.NodeBuilderService()
        project = sys_uuid.uuid4()
        target = compute_models.Volume(size=20, project_id=project, ephemeral=False)
        actual = compute_models.Volume(size=10, project_id=project)
        with pytest.raises(ValueError, match="unsupported"):
            service._actualize_volume(target, actual)
        assert actual.size == 10
