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

from exordos_core.storage import constants as sc
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
            "endpoint": node.driver_spec.endpoint,
            "weight": node.driver_spec.weight,
            "failure_domain_path": node.driver_spec.failure_domain_path,
        }
        for node in nodes
        if node.agent is None or node.status == "ACTIVE"
    }
    spec["pools"] = {
        str(policy.uuid): {
            **{
                field: getattr(policy, field) for field in ("name", "tier", "ephemeral")
            },
            **{
                field: getattr(policy.driver_spec, field)
                for field in ("mirrors", "chunk_size", "failure_domain")
            },
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
        if cluster.status != sc.CLUSTER_DELETING:
            cluster.status = pool.MachinePoolStatus.IN_PROGRESS.value
        cluster.update()


def finish_cluster_deletion(cluster):
    """Keep MDS until the OST targets and actual resources have disappeared."""
    if models.StorageNode.objects.get_all(
        filters={"cluster": filters.EQ(cluster.uuid)}
    ):
        return False
    for resource_model in (ua_models.TargetResource, ua_models.Resource):
        if any(
            str(r.value.get("cluster")) == str(cluster.uuid)
            for r in resource_model.objects.get_all(
                filters={"kind": filters.EQ("storage_node")}
            )
        ):
            return False
    for policy in models.StoragePool.objects.get_all(
        filters={"cluster": filters.EQ(cluster.uuid)}
    ):
        policy.delete()
    cluster.delete()
    return True
