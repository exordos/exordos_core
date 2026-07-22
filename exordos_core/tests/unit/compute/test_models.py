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
from unittest.mock import MagicMock
from unittest.mock import patch

from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.agents.universal.drivers import pool as ua_pool
from gcl_sdk.infra.dm import models as infra_models
from restalchemy.storage import exceptions as ra_storage_exceptions

from exordos_core.compute.dm import models


def _local_hyper_pool(node_uuid: sys_uuid.UUID) -> models.MachinePool:
    return models.MachinePool(
        driver_spec=ua_pool.ExordosLocalHyperDriverSpec(
            connection_uri="qemu:///system",
            node=node_uuid,
        ),
    )


def _patched_objects(**methods):
    # NodeEncryptionKey.objects is a descriptor that builds a fresh
    # ObjectCollection on every access, so patching one already-fetched
    # instance doesn't affect the production code's own separate access.
    # Replace the class-level "objects" attribute itself instead.
    return patch.object(ua_models.NodeEncryptionKey, "objects", MagicMock(**methods))


class TestMachinePoolInsert:
    def test_creates_a_key_when_none_exists(self):
        node_uuid = sys_uuid.uuid4()
        pool = _local_hyper_pool(node_uuid)
        not_found = ra_storage_exceptions.RecordNotFound(model=None, filters={})

        with (
            patch.object(models.orm.SQLStorableMixin, "insert"),
            _patched_objects(get_one=MagicMock(side_effect=not_found)),
            patch.object(ua_models.NodeEncryptionKey, "insert") as key_insert,
        ):
            pool.insert(agent_private_key="a-generated-key")

        key_insert.assert_called_once()

    def test_reuses_an_existing_key_when_none_explicitly_given(self):
        node_uuid = sys_uuid.uuid4()
        pool = _local_hyper_pool(node_uuid)
        existing = MagicMock(private_key="already-there")

        with (
            patch.object(models.orm.SQLStorableMixin, "insert"),
            _patched_objects(get_one=MagicMock(return_value=existing)),
            patch.object(ua_models.NodeEncryptionKey, "insert") as key_insert,
        ):
            pool.insert()

        key_insert.assert_not_called()
        existing.save.assert_not_called()

    def test_updates_an_existing_key_that_differs_from_the_explicit_one(self):
        # A caller that already generated and deployed a specific key (e.g.
        # bootstrap) must end up in sync with the DB - an existing key from
        # unrelated prior provisioning (e.g. this node's own compute Node)
        # would otherwise silently win, leaving the agent unable to
        # authenticate with the key it was actually given.
        node_uuid = sys_uuid.uuid4()
        pool = _local_hyper_pool(node_uuid)
        existing = MagicMock(private_key="stale-key")

        with (
            patch.object(models.orm.SQLStorableMixin, "insert"),
            _patched_objects(get_one=MagicMock(return_value=existing)),
        ):
            pool.insert(agent_private_key="fresh-key")

        assert existing.private_key == "fresh-key"
        existing.save.assert_called_once()

    def test_does_not_touch_the_db_for_non_local_hypervisors(self):
        pool = models.MachinePool(
            driver_spec=ua_pool.LibvirtPoolDriverSpec(
                connection_uri="qemu+tcp://127.0.0.1/system",
            ),
        )

        with (
            patch.object(models.orm.SQLStorableMixin, "insert"),
            _patched_objects() as mocked_objects,
        ):
            pool.insert()

        mocked_objects.get_one.assert_not_called()


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
