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
import typing as tp

from restalchemy.dm import filters as dm_filters

from exordos_core.compute.dm import models
from exordos_core.compute.scheduler.driver import base


class DummySoftAntiAffinityFilter(base.MachinePoolAbstractFilter):
    def filter(
        self,
        node: base.NodeBundle,
        pools: tp.List[base.MachinePoolBundle],
    ) -> tp.Iterable[base.MachinePoolBundle]:
        """Filter out pools that are not suitable for the node."""
        allocations = models.PlacementPolicyAllocation.objects.get_all(
            filters={
                "node": dm_filters.EQ(node.node.uuid),
            }
        )

        # If no policies, we don't have any constraints
        if not allocations:
            return pools

        for allocation in allocations:
            policy = allocation.policy
            members = models.PlacementPolicyAllocation.objects.get_all(
                filters={"policy": dm_filters.EQ(policy.uuid)}
            )
            machines = models.Machine.objects.get_all(
                filters={
                    "node": dm_filters.In(
                        a.node.uuid for a in members if a.node.uuid != node.node.uuid
                    )
                }
            )
            occupied = {m.pool for m in machines}
            if policy.scope == "site":
                used = {
                    p.site or str(p.uuid)
                    for p in models.MachinePool.objects.get_all(
                        filters={"uuid": dm_filters.In(occupied)}
                    )
                }
                available = tuple(
                    p for p in pools if (p.pool.site or str(p.pool.uuid)) not in used
                )
            else:
                available = tuple(p for p in pools if p.pool.uuid not in occupied)
            if available or policy.kind == "anti-affinity":
                pools = available
        return pools
