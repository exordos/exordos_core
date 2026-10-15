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
        uuid=uuid.uuid4(),
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
    volume = SimpleNamespace(
        uuid=uuid.uuid4(), tier="WARM", ephemeral=True, storage_pool="duplicate"
    )
    assert (
        service._select_storage_pool(SimpleNamespace(storage_pools=pools), volume, 1)
        is None
    )
    assert all(pool.capacity_provisioned == 0 for pool in pools)


@pytest.fixture(autouse=True)
def no_storage_clusters(monkeypatch):
    from exordos_core.storage.scheduler import select

    monkeypatch.setattr(select, "_active_clusters", lambda: [])


@pytest.mark.parametrize("other_size,allowed", [(0, True), (5, False)])
def test_builder_does_not_charge_own_cluster_reservation_twice(other_size, allowed):
    import time

    from gcl_sdk.agents.universal.drivers import rawstor_specs

    from exordos_core.storage.scheduler import select

    service = builder.PoolBuilderService(uuid.uuid4(), Mock())
    storage_pool = ua_pool.ThinStoragePool(
        uuid=uuid.uuid4(),
        name="persistent",
        pool_type="rawstor",
        tier="COLD",
        ephemeral=False,
        driver_spec=rawstor_specs.RawstorPoolDriverSpec(
            mirrors=2, chunk_size=1 << 30, failure_domain="server"
        ),
    )
    cluster = SimpleNamespace(
        uuid=uuid.uuid4(),
        storage_pools=[storage_pool],
        capacity_info={
            "reported_at": time.time(),
            "objects": {},
            "nodes": [
                {
                    "uuid": str(uuid.uuid4()),
                    "available": 9 << 30,
                    "weight": 100,
                    "failure_domain_path": domain,
                }
                for domain in ("one", "two")
            ],
        },
    )
    volume = SimpleNamespace(
        uuid=uuid.uuid4(),
        size=5,
        tier="COLD",
        ephemeral=False,
        storage_pool=str(storage_pool.uuid),
        pool=SimpleNamespace(storage_pools=[]),
    )
    reservations = [volume]
    if other_size:
        reservations.append(SimpleNamespace(uuid=uuid.uuid4(), size=other_size))
    with (
        patch.object(select, "_active_clusters", return_value=[cluster]),
        patch.object(
            select.compute_models.MachineVolume,
            "objects",
            Mock(get_all=Mock(return_value=reservations)),
        ),
    ):
        assert service._has_enough_space_in_pool(volume.pool, volume) is allowed
        if allowed:
            assert service._select_storage_pool(volume.pool, volume, 5)[1] is cluster
        # New placements must still account for this volume's reservation.
        assert select.select_storage_pool_with_clusters([], "COLD", False, 5) is None
