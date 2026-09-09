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
from unittest.mock import patch
import uuid as sys_uuid

from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.infra import constants as ic
from gcl_sdk.infra.dm import models as infra_models
import pytest
from restalchemy.common import exceptions

from exordos_core.compute.dm import models


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
            patch.object(models.QuotaModelMixin, "insert"),
            patch.object(models.Volume, "insert"),
            patch.object(ua_models.NodeEncryptionKey, "get_or_create") as get_or_create,
        ):
            node.insert()

        get_or_create.assert_called_once_with(node.uuid, session=None)


class TestVolumeSpeedEphemeralReadOnly:
    """Changing a disk's speed/ephemeral tier after creation would mean
    migrating it to a different storage pool, which isn't implemented -
    review feedback on PR #623 (akremenetsky) asked for this to fail
    loudly instead of being silently accepted with no actual effect.
    """

    def test_speed_and_ephemeral_are_settable_at_creation(self):
        volume = models.Volume(
            size=10,
            project_id=sys_uuid.uuid4(),
            speed=ic.DiskSpeed.HOT.value,
            ephemeral=True,
        )

        assert volume.speed == ic.DiskSpeed.HOT.value
        assert volume.ephemeral is True

    def test_speed_cannot_be_changed_after_creation(self):
        volume = models.Volume(size=10, project_id=sys_uuid.uuid4())

        with pytest.raises(exceptions.ReadOnlyProperty):
            volume.speed = ic.DiskSpeed.HOT.value

    def test_ephemeral_cannot_be_changed_after_creation(self):
        volume = models.Volume(size=10, project_id=sys_uuid.uuid4())

        with pytest.raises(exceptions.ReadOnlyProperty):
            volume.ephemeral = True
