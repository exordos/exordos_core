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

import typing as tp
import uuid as sys_uuid

from gcl_sdk.infra.dm import models as sdk_models
import pytest
from restalchemy.dm import filters as dm_filters

from exordos_core.common import constants as c
from exordos_core.compute.builders import node as node_builder
from exordos_core.compute.dm import models


class TestNodeBuilderService:
    def setup_method(self) -> None:
        self._service = node_builder.NodeBuilderService()

    def _add_node(self, disks: tp.List[dict]) -> models.Node:
        node = models.Node(
            uuid=sys_uuid.uuid4(),
            name="foo-node",
            cores=1,
            ram=1024,
            disk_spec=sdk_models.DisksSpec(disks=disks),
            project_id=c.SERVICE_PROJECT_ID,
        )
        node.insert()
        return node

    def _node_copy_with_disks(
        self, node: models.Node, disks: tp.List[dict]
    ) -> models.Node:
        return models.Node(
            uuid=node.uuid,
            name=node.name,
            cores=node.cores,
            ram=node.ram,
            disk_spec=sdk_models.DisksSpec(disks=disks),
            project_id=node.project_id,
        )

    def _node_volumes(self, node_uuid: sys_uuid.UUID) -> tp.List[models.Volume]:
        return list(
            models.Volume.objects.get_all(
                filters={"node": dm_filters.EQ(node_uuid)},
            )
        )

    @pytest.mark.usefixtures("user_api_client", "auth_user_admin")
    def test_add_and_remove_extra_disk(self):
        root_disk = {"size": 8, "image": "ubuntu_24.04"}
        data_disk = {"size": 20, "label": "data"}

        actual_node = self._add_node(disks=[root_disk])
        volumes = self._node_volumes(actual_node.uuid)
        assert len(volumes) == 1

        # Add a data disk to the node
        target_with_data = self._node_copy_with_disks(
            actual_node, disks=[root_disk, data_disk]
        )
        self._service._update_volumes(target_with_data, actual_node)

        volumes = self._node_volumes(actual_node.uuid)
        assert len(volumes) == 2
        added_volume = next(v for v in volumes if v.label == "data")
        assert added_volume.size == 20
        assert added_volume.project_id == actual_node.volume_project_id

        # Remove the data disk from the node
        target_without_data = self._node_copy_with_disks(actual_node, disks=[root_disk])
        self._service._update_volumes(target_without_data, target_with_data)

        volumes = self._node_volumes(actual_node.uuid)
        assert len(volumes) == 1
        assert volumes[0].label != "data"

        volumes[0].delete()
        actual_node.delete()
