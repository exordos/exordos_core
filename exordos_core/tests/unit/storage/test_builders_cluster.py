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
from gcl_sdk.agents.universal.drivers import rawstor_specs

from exordos_core.storage.builders import cluster as cluster_builder
from exordos_core.storage.dm import models


def _cluster(**overrides):
    kwargs = dict(
        uuid=sys_uuid.uuid4(),
        name="cluster1",
        driver_spec=ua_pool.RawstorStorageClusterDriverSpec(
            endpoint="mds://core:7776/",
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

        with (
            patch.object(models.StorageCluster, "_get_engine"),
            patch.object(cluster_builder.volumes, "sync_targets") as sync,
        ):
            context = service.prepare_iteration()

        assert context == {"clause_filters": {"builder": builder_uuid}}
        sync.assert_called_once_with(builder_uuid)

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

        with patch.object(
            cluster_builder.state, "agent_driver_spec", return_value=actual.driver_spec
        ):
            service.actualize_outdated_instance(current, actual)

        assert current.status == ua_pool.MachinePoolStatus.ACTIVE.value
        assert current.storage_pools == [pool]


def test_ready_ost_is_added_to_topology_only_for_its_current_configuration():
    service = cluster_builder.StorageClusterBuilderService.__new__(
        cluster_builder.StorageClusterBuilderService
    )
    current = models.Node(
        uuid=sys_uuid.uuid4(),
        name="ost",
        cluster=sys_uuid.uuid4(),
        driver_spec=rawstor_specs.RawstorNodeDriverSpec(
            endpoint="ost://host:7778",
            failure_domain_path="server",
            location="file:///data/ost",
            bind_address="0.0.0.0:7778",
        ),
        agent=sys_uuid.uuid4(),
    )
    actual = models.Node.restore_from_simple_view(**current.dump_to_simple_view())
    actual.status = "ACTIVE"
    actual.driver_spec.endpoint = "ost://host:7777"
    with (
        patch.object(models.Node, "update"),
        patch.object(models.StorageCluster, "objects"),
        patch.object(cluster_builder.state, "sync_cluster") as sync,
    ):
        service.actualize_outdated_instance(current, actual)
        assert current.status == "IN_PROGRESS"
        actual.driver_spec.endpoint = current.driver_spec.endpoint
        service.actualize_outdated_instance(current, actual)
        assert current.status == "ACTIVE"
        assert sync.call_count == 2


def test_cluster_stays_in_progress_until_agent_applies_current_spec():
    service = cluster_builder.StorageClusterBuilderService.__new__(
        cluster_builder.StorageClusterBuilderService
    )
    current = _cluster(status="IN_PROGRESS")
    actual = _cluster(status="ACTIVE")
    with patch.object(
        cluster_builder.state,
        "agent_driver_spec",
        return_value=ua_pool.RawstorStorageClusterDriverSpec(
            endpoint="mds://different:7776/"
        ),
    ):
        service.actualize_outdated_instance(current, actual)
    assert current.status == "IN_PROGRESS"


def test_cluster_status_compares_enriched_agent_snapshot():
    service = cluster_builder.StorageClusterBuilderService.__new__(
        cluster_builder.StorageClusterBuilderService
    )
    current = _cluster(status="IN_PROGRESS")
    spec = ua_pool.RawstorStorageClusterAgentSpec(endpoint="mds://core:7776/")
    value = current.dump_to_simple_view()
    value.update(status="ACTIVE", driver_spec=spec.dump_to_simple_view())
    actual = models.Cluster.from_ua_resource(SimpleNamespace(value=value))
    with patch.object(cluster_builder.state, "agent_driver_spec", return_value=spec):
        service.actualize_outdated_instance(current, actual)
        assert current.status == "ACTIVE"
        spec.nodes = {
            str(sys_uuid.uuid4()): {
                "endpoint": "ost://host:7777",
                "failure_domain_path": "server",
            }
        }
        service.actualize_outdated_instance(current, actual)
        assert current.status == "IN_PROGRESS"


def test_domain_change_waits_for_ost_to_apply_registration_settings():
    identifier, cluster, agent = sys_uuid.uuid4(), sys_uuid.uuid4(), sys_uuid.uuid4()

    def node(path):
        return models.Node(
            uuid=identifier,
            name="ost",
            cluster=cluster,
            agent=agent,
            status="ACTIVE",
            driver_spec=ua_pool.rawstor_specs.RawstorNodeDriverSpec(
                endpoint="ost://host:7777",
                bind_address="host:7777",
                location="file:///data/ost",
                failure_domain_path=path,
            ),
        )

    current, actual = node("rack/host"), node("rack/old-host")
    service = cluster_builder.StorageClusterBuilderService.__new__(
        cluster_builder.StorageClusterBuilderService
    )
    with (
        patch.object(current, "update"),
        patch.object(
            models.StorageCluster,
            "objects",
            MagicMock(get_one=MagicMock(return_value=MagicMock())),
        ),
        patch.object(cluster_builder.state, "sync_cluster"),
    ):
        service.actualize_outdated_instance(current, actual)
    assert current.status == "IN_PROGRESS"


def test_ost_deletion_does_not_depend_on_mds_topology():
    service = cluster_builder.StorageClusterBuilderService.__new__(
        cluster_builder.StorageClusterBuilderService
    )
    service._instance_model = models.Node
    resource = SimpleNamespace(kind="storage_node", uuid=sys_uuid.uuid4(), value={})
    assert service.can_delete_instance_resource(resource)
