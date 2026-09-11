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
from gcl_sdk.infra import constants as ic
import pytest

from exordos_core.compute.dm import models
from exordos_core.compute.scheduler import service
from exordos_core.compute.scheduler.driver import base
from exordos_core.storage.dm import models as storage_models
from exordos_core.storage.scheduler import select as storage_select


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


def _storage_cluster(name, speed, ephemeral, capacity_usable, endpoint="ost://cl:7777"):
    from gcl_sdk.agents.universal.drivers import pool as pool_base

    cluster = storage_models.StorageCluster(
        uuid=sys_uuid.uuid4(),
        name=name,
        driver_spec=pool_base.RawstorStorageClusterDriverSpec(
            location="file:///var/lib/rawstor",
            endpoint=endpoint,
            speed=speed,
            ephemeral=ephemeral,
        ),
        status=ua_pool.MachinePoolStatus.ACTIVE.value,
        agent=sys_uuid.uuid4(),
        storage_pools=[_storage_pool(name, speed, ephemeral, capacity_usable)],
    )
    cluster.save = lambda: None
    return cluster


@pytest.fixture(autouse=True)
def no_storage_clusters(monkeypatch):
    """By default there are no StorageClusters - placement only ever
    considers the pool's own local storage_pools, same as before
    two-tier scheduling was introduced. Tests that care about clusters
    override this explicitly.
    """
    monkeypatch.setattr(storage_select, "_active_clusters", lambda: [])


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
            "warm-pool",
            ic.DiskSpeed.WARM.value,
            False,
            capacity_usable=10,
            capacity_provisioned=10,
        )
        cold_pool = _storage_pool(
            "cold-pool",
            ic.DiskSpeed.COLD.value,
            False,
            capacity_usable=20,
            capacity_provisioned=19,
        )

        warm_volume = _machine_volume(
            "img1", 10, ic.DiskSpeed.WARM.value, False, "warm-pool"
        )
        cold_volume = _machine_volume(
            "img1", 19, ic.DiskSpeed.COLD.value, False, "cold-pool"
        )

        pool = _machine_pool_bundle([warm_pool, cold_pool], [warm_volume, cold_volume])
        requested = _requested_volume("img1", 20, ic.DiskSpeed.WARM.value, False)

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
            "warm-pool",
            ic.DiskSpeed.WARM.value,
            False,
            capacity_usable=10,
            capacity_provisioned=10,
        )
        warm_volume = _machine_volume(
            "img1", 10, ic.DiskSpeed.WARM.value, False, "warm-pool"
        )
        pool = _machine_pool_bundle([warm_pool], [warm_volume])
        requested = _requested_volume("img1", 20, ic.DiskSpeed.WARM.value, False)

        with pytest.raises(ValueError):
            scheduler._place_volume_into_pool(requested, pool)


class TestPlaceVolumeIntoPoolActualTier:
    """Reuse candidates must be classified by the tier of the pool they
    actually live on, not the tier they were originally requested with
    (Codex review on PR #623).
    """

    def test_prefers_the_volume_actually_on_the_matching_pool(self, scheduler):
        hot_pool = _storage_pool(
            "hot-pool", ic.DiskSpeed.HOT.value, False, capacity_usable=100
        )
        cold_pool = _storage_pool(
            "cold-pool", ic.DiskSpeed.COLD.value, False, capacity_usable=100
        )

        # Recorded as "hot" (the original request) but a soft-match
        # fallback actually placed it on cold-pool.
        misplaced = _machine_volume(
            "img1", 10, ic.DiskSpeed.HOT.value, False, "cold-pool"
        )
        # Recorded as "cold" but actually sitting on hot-pool.
        genuine = _machine_volume(
            "img1", 10, ic.DiskSpeed.COLD.value, False, "hot-pool"
        )

        pool = _machine_pool_bundle([hot_pool, cold_pool], [misplaced, genuine])
        requested = _requested_volume("img1", 10, ic.DiskSpeed.HOT.value, False)

        result = scheduler._place_volume_into_pool(requested, pool)

        assert result.uuid == genuine.uuid


class TestPlaceVolumeIntoPoolBrokenCandidates:
    """Review feedback on PR #623 (Sourcery/Codex + akremenetsky): a
    reuse candidate whose actual storage pool can't be determined
    (predates storage_pool tracking, or was pinned to a pool that no
    longer exists) is skipped rather than guessed at - a scheduled
    volume always knows where it lives, so this should be rare, and
    reusing the wrong one would silently misaccount capacity.
    """

    def test_skips_a_candidate_whose_pool_no_longer_exists(self, scheduler):
        # "ghost-pool" was renamed/removed after this volume was pinned
        # to it - _find_storage_pool_by_name can no longer resolve it.
        good_pool = _storage_pool(
            "good-pool", ic.DiskSpeed.WARM.value, False, capacity_usable=100
        )
        stale_volume = _machine_volume(
            "img1", 10, ic.DiskSpeed.WARM.value, False, "ghost-pool"
        )
        good_volume = _machine_volume(
            "img1", 10, ic.DiskSpeed.WARM.value, False, "good-pool"
        )

        pool = _machine_pool_bundle([good_pool], [stale_volume, good_volume])
        requested = _requested_volume("img1", 10, ic.DiskSpeed.WARM.value, False)

        # Must not raise despite the first candidate's pool being
        # unresolvable - it must be skipped in favor of the next one.
        result = scheduler._place_volume_into_pool(requested, pool)

        assert result.uuid == good_volume.uuid
        assert result.storage_pool == "good-pool"

    def test_legacy_volume_with_no_pinned_pool_is_not_reused(self, scheduler):
        # Can't tell which pool a legacy (pre-migration) volume with
        # storage_pool unset actually lives on - rather than guessing
        # via a fresh soft match, skip it and create a new volume.
        only_pool = _storage_pool(
            "only-pool", ic.DiskSpeed.WARM.value, False, capacity_usable=100
        )
        legacy_volume = models.MachineVolume(
            uuid=sys_uuid.uuid4(),
            project_id=sys_uuid.uuid4(),
            size=10,
            image="img1",
            speed=ic.DiskSpeed.WARM.value,
            ephemeral=False,
            storage_pool=None,
        )

        pool = _machine_pool_bundle([only_pool], [legacy_volume])
        requested = _requested_volume("img1", 10, ic.DiskSpeed.WARM.value, False)

        result = scheduler._place_volume_into_pool(requested, pool)

        assert result.uuid != legacy_volume.uuid
        assert result.storage_pool == "only-pool"
        # The legacy volume is left alone, not consumed.
        assert legacy_volume in pool.volumes


class TestPlaceVolumeIntoPoolWithClusters:
    """Local pools and active StorageClusters are equal candidates - the
    winner is whichever has more free capacity, not whichever is local.
    """

    def test_a_cluster_pool_wins_when_it_has_more_free_space(
        self, scheduler, monkeypatch
    ):
        small_local = _storage_pool(
            "small-local", ic.DiskSpeed.WARM.value, False, capacity_usable=20
        )
        big_cluster = _storage_cluster(
            "big-cluster", ic.DiskSpeed.WARM.value, False, capacity_usable=100
        )
        monkeypatch.setattr(storage_select, "_active_clusters", lambda: [big_cluster])

        pool = _machine_pool_bundle([small_local], [])
        requested = _requested_volume("img1", 10, ic.DiskSpeed.WARM.value, False)

        result = scheduler._place_volume_into_pool(requested, pool)

        assert result.storage_pool == "big-cluster"
        assert result.storage_location == "ost://cl:7777"

    def test_a_local_pool_is_used_when_no_cluster_fits(self, scheduler, monkeypatch):
        local_pool = _storage_pool(
            "local", ic.DiskSpeed.WARM.value, False, capacity_usable=100
        )
        small_cluster = _storage_cluster(
            "small-cluster", ic.DiskSpeed.WARM.value, False, capacity_usable=5
        )
        monkeypatch.setattr(storage_select, "_active_clusters", lambda: [small_cluster])

        pool = _machine_pool_bundle([local_pool], [])
        requested = _requested_volume("img1", 10, ic.DiskSpeed.WARM.value, False)

        result = scheduler._place_volume_into_pool(requested, pool)

        assert result.storage_pool == "local"
        assert result.storage_location is None

    def test_only_a_cluster_is_available_and_no_local_pools_configured(
        self, scheduler, monkeypatch
    ):
        cluster = _storage_cluster(
            "only-cluster", ic.DiskSpeed.HOT.value, False, capacity_usable=50
        )
        monkeypatch.setattr(storage_select, "_active_clusters", lambda: [cluster])

        pool = _machine_pool_bundle([], [])
        requested = _requested_volume("img1", 10, ic.DiskSpeed.HOT.value, False)

        result = scheduler._place_volume_into_pool(requested, pool)

        assert result.storage_pool == "only-cluster"
        assert result.storage_location == "ost://cl:7777"
