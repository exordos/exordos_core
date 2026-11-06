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

import time
import typing as tp

from gcl_sdk.agents.universal.drivers import pool as ua_pool
from gcl_sdk.agents.universal.drivers import storage_capacity
from restalchemy.dm import filters as dm_filters

from exordos_core.compute.dm import models as compute_models
from exordos_core.storage.dm import models as storage_models

# A pool paired with the StorageCluster it belongs to, or None for a
# hypervisor's own local pool.
PoolCandidate = tp.Tuple[
    ua_pool.AbstractStoragePool, tp.Optional[storage_models.StorageCluster]
]


def _active_clusters() -> tp.List[storage_models.StorageCluster]:
    return storage_models.StorageCluster.objects.get_all(
        filters={
            "status": dm_filters.EQ(ua_pool.MachinePoolStatus.ACTIVE.value),
            "agent": dm_filters.IsNot(None),
        },
    )


def collect_storage_pool_candidates(
    local_pools: tp.Iterable[ua_pool.AbstractStoragePool],
    staged=None,
    exclude_volume=None,
) -> tp.List[PoolCandidate]:
    """Pair every candidate pool with the StorageCluster that owns it.

    A hypervisor's own local pools and every active StorageCluster's
    pools are equal candidates - neither is preferred over the other,
    per the operator's choice recorded in the storages feature plan.
    """
    candidates: tp.List[PoolCandidate] = [(p, None) for p in local_pools]
    for cluster in _active_clusters():
        if cluster.storage_pools:
            info = cluster.capacity_info
            if time.time() - info.get("reported_at", 0) > 60:
                continue
            volumes = compute_models.MachineVolume.objects.get_all(
                filters={
                    "storage_pool": dm_filters.In(
                        [str(p.uuid) for p in cluster.storage_pools]
                    )
                }
            )
            promised = {str(v.uuid): v.size << 30 for v in volumes}
            promised.update((staged or {}).get(str(cluster.uuid), {}))
            observed = info.get("objects", {})
            pending = [
                max(0, size - observed.get(uuid, 0))
                for uuid, size in promised.items()
                if uuid != str(exclude_volume)
            ]
            for pool in cluster.storage_pools:
                policy = {
                    "mirrors": pool.driver_spec.mirrors,
                    "chunk_size": pool.driver_spec.chunk_size,
                    "failure_domain": pool.driver_spec.failure_domain,
                }
                available = (
                    storage_capacity.available_by_policy(
                        info.get("nodes", []), policy, pending
                    )
                    >> 30
                )
                pool.capacity_usable = available
                pool.capacity_provisioned = 0
                pool.available_actual = available
        candidates.extend((p, cluster) for p in cluster.storage_pools)
    return candidates


def select_storage_pool_with_clusters(
    local_pools: tp.Iterable[ua_pool.AbstractStoragePool],
    tier: str,
    ephemeral: bool,
    size: int,
    assigned_name: tp.Optional[str] = None,
    staged=None,
    exclude_volume=None,
) -> tp.Optional[PoolCandidate]:
    """Like `ua_pool.select_storage_pool`, but drawing from local pools
    and active StorageClusters' pools together (see
    `collect_storage_pool_candidates`).
    """
    candidates = collect_storage_pool_candidates(
        local_pools, staged, exclude_volume=exclude_volume
    )

    if assigned_name is not None:
        assigned = [
            (p, c)
            for p, c in candidates
            if (str(p.uuid) if c else p.name) == assigned_name
        ]
        if len(assigned) != 1 or not assigned[0][0].has_capacity(size):
            return None
        return assigned[0]

    # Local pools are addressed by name; cluster pools are addressed by UUID.
    # Identical names in distinct clusters therefore remain valid candidates.
    local_names = [pool.name for pool, owner in candidates if owner is None]
    eligible = [
        (pool, owner)
        for pool, owner in candidates
        if pool.ephemeral == ephemeral
        and pool.has_capacity(size)
        and (owner is not None or local_names.count(pool.name) == 1)
    ]
    exact = [(pool, owner) for pool, owner in eligible if pool.tier == tier]
    return max(exact or eligible, key=lambda entry: entry[0].available, default=None)


def find_storage_pool_by_name_with_clusters(
    local_pools: tp.Iterable[ua_pool.AbstractStoragePool],
    name: str,
) -> tp.Optional[PoolCandidate]:
    """Find a pool already assigned to a volume by name.

    Raises `ValueError` if the name is ambiguous - accounting capacity
    against the wrong physical pool would be worse than failing loudly.
    """
    matches = [
        (pool, owner)
        for pool, owner in collect_storage_pool_candidates(local_pools)
        if (str(pool.uuid) if owner else pool.name) == name
    ]

    if not matches:
        return None

    if len(matches) > 1:
        raise ValueError(f"Storage pool name {name!r} is not unique")

    return matches[0]
