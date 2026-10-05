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

from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import patch
import uuid as sys_uuid

from gcl_sdk.agents.universal.drivers import pool as ua_pool
from gcl_sdk.infra import constants as ic
import pytest

from exordos_core.storage.builders import cluster as cluster_builder
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
        status=ua_pool.MachinePoolStatus.DISABLED.value,
    )
    kwargs.update(overrides)
    return models.Cluster(**kwargs)


class TestStorageClusterBuilderService:
    def test_prepare_iteration_filters_by_this_builder(self):
        builder_uuid = sys_uuid.uuid4()
        service = cluster_builder.StorageClusterBuilderService.__new__(
            cluster_builder.StorageClusterBuilderService
        )
        service._service_spec = type("Spec", (), {"uuid": builder_uuid})()

        with patch.object(models.StorageCluster, "_get_engine"):
            context = service.prepare_iteration()

        assert context == {"clause_filters": {"builder": builder_uuid}}

    def test_actualize_outdated_instance_syncs_capacity_and_status(self):
        service = cluster_builder.StorageClusterBuilderService.__new__(
            cluster_builder.StorageClusterBuilderService
        )

        pool = ua_pool.ThinStoragePool(
            name="default", pool_type="rawstor", capacity_usable=50
        )
        current = _cluster(status=ua_pool.MachinePoolStatus.DISABLED.value)
        actual = _cluster(
            status=ua_pool.MachinePoolStatus.ACTIVE.value,
            storage_pools=[pool],
        )

        service.actualize_outdated_instance(current, actual)

        assert current.status == ua_pool.MachinePoolStatus.ACTIVE.value
        assert current.storage_pools == [pool]


@pytest.mark.parametrize(
    "actual_nodes,target_nodes,same_hash,allowed",
    [
        ({"ost": {}}, {}, False, False),
        ({"ost": {}}, {}, True, False),
        ({}, {"ost": {}}, False, False),
        ({}, {}, False, False),
        ({}, {}, True, True),
    ],
)
def test_ost_deletion_waits_for_current_mds_topology(
    actual_nodes, target_nodes, same_hash, allowed
):
    service = cluster_builder.StorageClusterBuilderService.__new__(
        cluster_builder.StorageClusterBuilderService
    )
    resource = SimpleNamespace(
        kind="storage_node", uuid="ost", value={"cluster": str(sys_uuid.uuid4())}
    )
    actual = SimpleNamespace(
        hash="actual", value={"driver_spec": {"nodes": actual_nodes}}
    )
    target = SimpleNamespace(
        hash="actual" if same_hash else "pending",
        value={"driver_spec": {"nodes": target_nodes}},
    )
    # Hash equality also implies the target nodes match the actual nodes.
    if actual_nodes != target_nodes:
        target.hash = "pending"
    with (
        patch.object(
            cluster_builder.ua_models.Resource,
            "objects",
            MagicMock(get_all=MagicMock(return_value=[actual])),
        ),
        patch.object(
            cluster_builder.ua_models.TargetResource,
            "objects",
            MagicMock(get_all=MagicMock(return_value=[target])),
        ),
    ):
        assert service.can_delete_instance_resource(resource) is allowed


def test_ready_ost_is_added_to_topology_only_for_its_current_configuration():
    service = cluster_builder.StorageClusterBuilderService.__new__(
        cluster_builder.StorageClusterBuilderService
    )
    current = models.Node(
        uuid=sys_uuid.uuid4(),
        name="ost",
        cluster=sys_uuid.uuid4(),
        endpoint="ost://host:7778",
        failure_domain_path="server",
        location="file:///data/ost",
        bind_address="0.0.0.0:7778",
        agent=sys_uuid.uuid4(),
    )
    actual = models.Node.restore_from_simple_view(**current.dump_to_simple_view())
    actual.status = "ACTIVE"
    actual.endpoint = "ost://host:7777"
    with (
        patch.object(models.Node, "update"),
        patch.object(models.StorageCluster, "objects"),
        patch.object(cluster_builder.state, "sync_cluster") as sync,
    ):
        service.actualize_outdated_instance(current, actual)
        assert current.status == "IN_PROGRESS"
        actual.endpoint = current.endpoint
        service.actualize_outdated_instance(current, actual)
        assert current.status == "ACTIVE"
        assert sync.call_count == 2
