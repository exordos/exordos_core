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
from restalchemy.dm import filters as dm_filters

from exordos_core.storage.dm import models


def _cluster(**overrides):
    kwargs = dict(
        uuid=sys_uuid.uuid4(),
        name="cluster1",
        driver_spec=ua_pool.RawstorStorageClusterDriverSpec(
            location="file:///var/lib/rawstor",
            endpoint="ost://10.0.0.5:7777",
            speed=ic.DiskSpeed.HOT.value,
            ephemeral=False,
        ),
    )
    kwargs.update(overrides)
    return models.Cluster(**kwargs)


class TestStorageCluster:
    def test_defaults(self):
        cluster = _cluster()

        assert cluster.status == ua_pool.MachinePoolStatus.DISABLED.value
        assert cluster.storage_pools == []
        assert cluster.agent is None
        assert cluster.builder is None

    def test_rejects_a_missing_driver_spec(self):
        with pytest.raises(Exception):
            models.StorageCluster(uuid=sys_uuid.uuid4(), name="c")


class TestCluster:
    def test_get_resource_kind(self):
        assert models.Cluster.get_resource_kind() == "storage_cluster"

    def test_get_resource_target_fields_is_minimal(self):
        cluster = _cluster()

        # Unlike a hypervisor Pool, a storage cluster has no
        # machine-scheduling fields to propagate to the agent.
        assert cluster.get_resource_target_fields() == frozenset(
            ("uuid", "driver_spec")
        )

    def test_get_filter_clause_matches_by_builder(self):
        builder = sys_uuid.uuid4()
        clause = models.Cluster.get_filter_clause(builder)

        assert clause == {"builder": dm_filters.EQ(str(builder))}

    def test_schedule_to_ua_agent_returns_the_agent_field(self):
        agent = sys_uuid.uuid4()
        cluster = _cluster(agent=agent)

        assert cluster.schedule_to_ua_agent() == agent
