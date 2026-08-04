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
from restalchemy.storage.sql import orm

from exordos_core.compute import constants as nc
from exordos_core.compute.builders import node as node_builder
from exordos_core.compute.dm import models as compute_models


class TestNodeBuilderService:
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
