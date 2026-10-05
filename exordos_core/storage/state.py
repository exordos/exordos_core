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

"""Storage policy/topology snapshots sent to the core agent."""

from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.agents.universal.drivers import pool
from restalchemy.dm import filters

from exordos_core.storage.dm import models

# Serializes API topology edits and scheduler admission across processes.
STORAGE_LOCK = 21007776


def lock(session):
    session.execute("SELECT pg_advisory_xact_lock(%s)", (STORAGE_LOCK,))


def agent_driver_spec(cluster):
    nodes = models.StorageNode.objects.get_all(
        filters={"cluster": filters.EQ(cluster.uuid)}
    )
    policies = models.StoragePool.objects.get_all(
        filters={"cluster": filters.EQ(cluster.uuid)}
    )
    spec = cluster.driver_spec.dump_to_simple_view()
    spec["managed"] = True
    spec["nodes"] = {
        str(node.uuid): {
            "endpoint": node.endpoint,
            "weight": node.weight,
            "failure_domain_path": node.failure_domain_path,
        }
        for node in nodes
        if node.agent is None or node.status == "ACTIVE"
    }
    spec["pools"] = {
        str(policy.uuid): {
            field: getattr(policy, field)
            for field in (
                "name",
                "speed",
                "ephemeral",
                "mirrors",
                "chunk_size",
                "failure_domain",
            )
        }
        for policy in policies
    }
    return pool.RawstorStorageClusterAgentSpec.restore_from_simple_view(**spec)


def sync_cluster(cluster):
    # The builder regenerates the agent snapshot from nodes/pools. Never persist
    # that snapshot in the public cluster configuration.
    targets = ua_models.TargetResource.objects.get_all(
        filters={
            "uuid": filters.EQ(cluster.uuid),
            "kind": filters.EQ("storage_cluster"),
        }
    )
    snapshot = agent_driver_spec(cluster).dump_to_simple_view()
    if not targets or targets[0].value.get("driver_spec") != snapshot:
        cluster.status = pool.MachinePoolStatus.IN_PROGRESS.value
        cluster.update()
