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

from gcl_sdk.agents.universal.drivers import pool as ua_pool
from restalchemy.dm import filters as dm_filters

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
) -> tp.List[PoolCandidate]:
    """Pair every candidate pool with the StorageCluster that owns it.

    A hypervisor's own local pools and every active StorageCluster's
    pools are equal candidates - neither is preferred over the other,
    per the operator's choice recorded in the storages feature plan.
    """
    candidates: tp.List[PoolCandidate] = [(p, None) for p in local_pools]
    for cluster in _active_clusters():
        candidates.extend((p, cluster) for p in cluster.storage_pools)
    return candidates


def select_storage_pool_with_clusters(
    local_pools: tp.Iterable[ua_pool.AbstractStoragePool],
    speed: str,
    ephemeral: bool,
    size: int,
    assigned_name: tp.Optional[str] = None,
) -> tp.Optional[PoolCandidate]:
    """Like `ua_pool.select_storage_pool`, but drawing from local pools
    and active StorageClusters' pools together (see
    `collect_storage_pool_candidates`).
    """
    candidates = collect_storage_pool_candidates(local_pools)
    owner_by_id = {id(pool): owner for pool, owner in candidates}

    selected = ua_pool.select_storage_pool(
        (pool for pool, _owner in candidates), speed, ephemeral, size, assigned_name
    )
    if selected is None:
        return None

    return selected, owner_by_id[id(selected)]


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
        if pool.name == name
    ]

    if not matches:
        return None

    if len(matches) > 1:
        raise ValueError(f"Storage pool name {name!r} is not unique")

    return matches[0]
