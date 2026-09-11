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

import logging
import random
import typing as tp

from gcl_looper.services import basic
from gcl_sdk.agents.universal.dm import models as ua_models
from restalchemy.common import contexts
from restalchemy.dm import filters as dm_filters

from exordos_core.compute import constants as nc
from exordos_core.storage.dm import models as storage_models

LOG = logging.getLogger(__name__)
STORAGE_CLUSTER_CAP = "storage_cluster"


class StorageClusterSchedulerService(basic.BasicService):
    """Schedules StorageCluster resources onto builders/agents.

    Much simpler than compute's pool scheduling (see
    SchedulerService._schedule_pools): a storage cluster is never
    node-pinned, so any agent advertising the "storage_cluster"
    capability is eligible - no filters/weighters needed.
    """

    def _get_cluster_builders(
        self, limit: int = nc.DEF_SQL_LIMIT
    ) -> tp.List[ua_models.UniversalAgent]:
        """Get all active storage cluster builders."""
        return ua_models.UniversalAgent.objects.get_all(
            filters={
                "status": dm_filters.EQ(nc.BuilderStatus.ACTIVE.value),
                "name": dm_filters.Like("storage_cluster_builder%"),
            },
            limit=limit,
        )

    def _get_unscheduled_clusters(
        self, limit: int = nc.DEF_SQL_LIMIT
    ) -> tp.List[storage_models.StorageCluster]:
        """Get all unscheduled storage clusters."""
        return storage_models.StorageCluster.objects.get_all(
            filters={"builder": dm_filters.Is(None)},
            limit=limit,
        )

    def _iteration(self):
        with contexts.Context().session_manager():
            unscheduled = self._get_unscheduled_clusters()
            if not unscheduled:
                LOG.debug("Nothing to schedule, no unscheduled storage clusters")
                return

            builders = self._get_cluster_builders()
            if not builders:
                LOG.warning(
                    "No storage cluster builders found to schedule clusters %s",
                    [c.uuid for c in unscheduled],
                )
                return

            agents = ua_models.UniversalAgent.have_capabilities((STORAGE_CLUSTER_CAP,))
            available_agents = agents.get(STORAGE_CLUSTER_CAP, [])
            if not available_agents:
                LOG.warning(
                    "No storage cluster agents found to schedule clusters %s",
                    [c.uuid for c in unscheduled],
                )
                return

            for cluster in unscheduled:
                builder = random.choice(builders)
                agent = random.choice(available_agents)

                try:
                    cluster.builder = builder.uuid
                    cluster.agent = agent.uuid
                    cluster.update()
                    LOG.info(
                        "The storage cluster %s scheduled to builder %s and agent %s",
                        cluster.uuid,
                        builder.uuid,
                        agent.uuid,
                    )
                except Exception:
                    LOG.exception("Error scheduling storage cluster %s", cluster.uuid)
