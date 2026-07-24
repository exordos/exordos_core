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
import uuid as sys_uuid
from unittest.mock import patch

from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.agents.universal.drivers import pool as ua_pool
from gcl_sdk.infra.dm import models as infra_models

from exordos_core.compute.dm import models


class TestMachinePoolInsert:
    def test_never_touches_node_encryption_key(self):
        # MachinePool doesn't know about node encryption keys - whoever
        # creates a local-hypervisor pool (bootstrap, HypervisorsController)
        # is responsible for provisioning the key itself.
        pool = models.MachinePool(
            driver_spec=ua_pool.ExordosLocalHyperDriverSpec(
                connection_uri="qemu:///system",
                node=sys_uuid.uuid4(),
            ),
        )

        with (
            patch.object(models.orm.SQLStorableMixin, "insert"),
            patch.object(ua_models.NodeEncryptionKey, "get_or_create") as get_or_create,
        ):
            pool.insert()

        get_or_create.assert_not_called()


class TestNodeInsert:
    def test_reuses_an_existing_key(self):
        # A key may already exist for this uuid (e.g. it's also a local
        # hypervisor's node, which provisions its own key the same way) -
        # go through get_or_create instead of blindly inserting a fresh
        # one and conflicting on the unique node uuid.
        node = models.Node(
            cores=1,
            ram=1024,
            disk_spec=infra_models.RootDiskSpec(image="ubuntu_24.04"),
            project_id=sys_uuid.uuid4(),
        )

        with (
            patch.object(models.orm.SQLStorableMixin, "insert"),
            patch.object(ua_models.NodeEncryptionKey, "get_or_create") as get_or_create,
        ):
            node.insert()

        get_or_create.assert_called_once_with(node.uuid, session=None)
