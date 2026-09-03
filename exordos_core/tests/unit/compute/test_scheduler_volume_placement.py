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

from gcl_sdk.agents.universal.drivers import pool as ua_pool
import pytest

from exordos_core.compute.dm import models
from exordos_core.compute.scheduler import service
from exordos_core.compute.scheduler.driver import base


def _storage_pool(name, speed, ephemeral, capacity_usable, capacity_provisioned=0):
    return ua_pool.ThinStoragePool(
        name=name,
        pool_type="dir",
        speed=speed,
        ephemeral=ephemeral,
        capacity_usable=capacity_usable,
        capacity_provisioned=capacity_provisioned,
    )


def _machine_pool_bundle(storage_pools, volumes):
    machine_pool = models.MachinePool(
        uuid=sys_uuid.uuid4(),
        name="pool1",
        driver_spec=ua_pool.DummyPoolDriverSpec(),
        storage_pools=storage_pools,
    )
    return base.MachinePoolBundle(pool=machine_pool, volumes=volumes)


def _machine_volume(image, size, speed, ephemeral, storage_pool):
    return models.MachineVolume(
        uuid=sys_uuid.uuid4(),
        project_id=sys_uuid.uuid4(),
        size=size,
        image=image,
        speed=speed,
        ephemeral=ephemeral,
        storage_pool=storage_pool,
    )


def _requested_volume(image, size, speed, ephemeral):
    return models.Volume(
        uuid=sys_uuid.uuid4(),
        project_id=sys_uuid.uuid4(),
        size=size,
        image=image,
        speed=speed,
        ephemeral=ephemeral,
    )


@pytest.fixture
def scheduler():
    return service.SchedulerService(
        pool_filters=[],
        pool_weighters=[],
        machine_filters=[],
        machine_weighters=[],
    )


class TestPlaceVolumeIntoPoolFallback:
    """A full exact-tier candidate must not block reuse of a different,
    still-fitting candidate (Codex review on PR #623).
    """

    def test_falls_back_to_a_fitting_candidate_when_the_exact_match_is_full(
        self, scheduler
    ):
        warm_pool = _storage_pool(
            "warm-pool", "warm", False, capacity_usable=10, capacity_provisioned=10
        )
        cold_pool = _storage_pool(
            "cold-pool", "cold", False, capacity_usable=20, capacity_provisioned=19
        )

        warm_volume = _machine_volume("img1", 10, "warm", False, "warm-pool")
        cold_volume = _machine_volume("img1", 19, "cold", False, "cold-pool")

        pool = _machine_pool_bundle(
            [warm_pool, cold_pool], [warm_volume, cold_volume]
        )
        requested = _requested_volume("img1", 20, "warm", False)

        # The exact (warm) match has no room for the resize and no pool
        # has 20GiB free for a brand new volume either - reuse must fall
        # through to the cold volume instead of raising.
        result = scheduler._place_volume_into_pool(requested, pool)

        assert result.uuid == cold_volume.uuid
        assert cold_pool.available == 0
        assert warm_volume in pool.volumes

    def test_raises_when_the_project_predates_the_fix(self, scheduler):
        """Sanity check: with no fallback candidate at all, scheduling
        genuinely has nowhere to go and must still raise.
        """
        warm_pool = _storage_pool(
            "warm-pool", "warm", False, capacity_usable=10, capacity_provisioned=10
        )
        warm_volume = _machine_volume("img1", 10, "warm", False, "warm-pool")
        pool = _machine_pool_bundle([warm_pool], [warm_volume])
        requested = _requested_volume("img1", 20, "warm", False)

        with pytest.raises(ValueError):
            scheduler._place_volume_into_pool(requested, pool)


class TestPlaceVolumeIntoPoolActualTier:
    """Reuse candidates must be classified by the tier of the pool they
    actually live on, not the tier they were originally requested with
    (Codex review on PR #623).
    """

    def test_prefers_the_volume_actually_on_the_matching_pool(self, scheduler):
        hot_pool = _storage_pool("hot-pool", "hot", False, capacity_usable=100)
        cold_pool = _storage_pool("cold-pool", "cold", False, capacity_usable=100)

        # Recorded as "hot" (the original request) but a soft-match
        # fallback actually placed it on cold-pool.
        misplaced = _machine_volume("img1", 10, "hot", False, "cold-pool")
        # Recorded as "cold" but actually sitting on hot-pool.
        genuine = _machine_volume("img1", 10, "cold", False, "hot-pool")

        pool = _machine_pool_bundle(
            [hot_pool, cold_pool], [misplaced, genuine]
        )
        requested = _requested_volume("img1", 10, "hot", False)

        result = scheduler._place_volume_into_pool(requested, pool)

        assert result.uuid == genuine.uuid
