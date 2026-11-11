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


@pytest.mark.parametrize("size", [12, 30])
def test_resize_charges_only_the_pinned_pool(size):
    service = builder.PoolBuilderService(uuid.uuid4(), Mock())
    pinned = ua_pool.ThinStoragePool(
        pool_type="dir", name="pinned", capacity_usable=20, capacity_provisioned=10
    )
    other = ua_pool.ThinStoragePool(pool_type="dir", name="other", capacity_usable=1000)
    volume = SimpleNamespace(
        uuid=uuid.uuid4(),
        size=size,
        tier="WARM",
        ephemeral=True,
        storage_pool="pinned",
        pool=SimpleNamespace(storage_pools=[pinned, other]),
        status="ACTIVE",
        node_volume=Mock(),
        save=Mock(),
    )
    actual = SimpleNamespace(size=10)
    fits = size - actual.size <= pinned.available
    with patch.object(models.MachineVolume, "from_ua_resource", return_value=actual):
        assert service._can_update_volume(volume, Mock()) is fits
    assert volume.storage_pool == "pinned"
    assert other.capacity_provisioned == 0
    if fits:
        assert pinned.capacity_provisioned == 10 + size - actual.size
    else:
        assert pinned.capacity_provisioned == 10
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
    for model in (select.ua_models.TargetResource, select.ua_models.Resource):
        monkeypatch.setattr(model, "objects", Mock(get_all=Mock(return_value=[])))


@pytest.mark.parametrize("actual_size,target_size,available", [(0, 5, 9), (4, 8, 5)])
@pytest.mark.parametrize("other_size,allowed", [(0, True), (5, False)])
def test_builder_does_not_charge_own_cluster_reservation_twice(
    other_size, allowed, actual_size, target_size, available
):
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
        status="ACTIVE",
        uuid=uuid.uuid4(),
        storage_pools=[storage_pool],
        capacity_info={
            "reported_at": time.time(),
            "objects": {},
            "nodes": [
                {
                    "uuid": str(uuid.uuid4()),
                    "available": available << 30,
                    "weight": 100,
                    "failure_domain_path": domain,
                }
                for domain in ("one", "two")
            ],
        },
    )
    volume = SimpleNamespace(
        uuid=uuid.uuid4(),
        size=target_size,
        tier="COLD",
        ephemeral=False,
        storage_pool=str(storage_pool.uuid),
        pool=SimpleNamespace(
            storage_pools=[],
            driver_spec=ua_pool.ExordosLocalHyperDriverSpec(
                connection_uri="test:///default", node=uuid.uuid4()
            ),
        ),
    )
    cluster.capacity_info["objects"][str(volume.uuid)] = actual_size << 30
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
        assert (
            service._has_enough_space_in_pool(
                volume.pool, volume, SimpleNamespace(size=actual_size)
            )
            is allowed
        )
        if allowed:
            assert (
                service._select_storage_pool(
                    volume.pool, volume, target_size - actual_size
                )[1]
                is cluster
            )
        # New placements must still account for this volume's reservation.
        assert select.select_storage_pool_with_clusters([], "COLD", False, 5) is None
