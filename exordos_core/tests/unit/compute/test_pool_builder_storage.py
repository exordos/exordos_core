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

from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import patch
import uuid

from gcl_sdk.agents.universal.drivers import pool as ua_pool
import pytest

from exordos_core.compute.builders import pool as builder
from exordos_core.compute.pool.dm import models


@pytest.mark.parametrize("reported", ["historical", None])
def test_resize_recovers_only_the_pool_reported_by_data_plane(reported):
    service = builder.PoolBuilderService(uuid.uuid4(), Mock())
    historical = ua_pool.ThinStoragePool(
        pool_type="dir", name="historical", capacity_usable=20, capacity_provisioned=10
    )
    other = ua_pool.ThinStoragePool(pool_type="dir", name="other", capacity_usable=1000)
    volume = SimpleNamespace(
        size=12,
        tier="WARM",
        ephemeral=True,
        storage_pool=None,
        pool=SimpleNamespace(storage_pools=[historical, other]),
        status="ACTIVE",
        node_volume=Mock(),
        save=Mock(),
    )
    actual = SimpleNamespace(size=10, storage_pool=reported)
    with patch.object(models.MachineVolume, "from_ua_resource", return_value=actual):
        assert service._can_update_volume(volume, Mock()) is bool(reported)
    assert other.capacity_provisioned == 0
    if reported:
        assert volume.storage_pool == "historical"
        assert historical.capacity_provisioned == 12
    else:
        assert volume.storage_pool is None
        assert historical.capacity_provisioned == 10
        assert volume.status == "ERROR"


def test_resize_rejects_duplicate_pool_names():
    service = builder.PoolBuilderService(uuid.uuid4(), Mock())
    pools = [
        ua_pool.ThinStoragePool(pool_type="dir", name="duplicate", capacity_usable=size)
        for size in (10, 100)
    ]
    volume = SimpleNamespace(tier="WARM", ephemeral=True, storage_pool="duplicate")
    assert (
        service._select_storage_pool(SimpleNamespace(storage_pools=pools), volume, 1)
        is None
    )
    assert all(pool.capacity_provisioned == 0 for pool in pools)
