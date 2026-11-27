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

"""Optional PostgreSQL integration: migrations, topology and shared admission.

Set EXORDOS_STORAGE_TEST_DB to a dedicated PostgreSQL administrator DSN.
Each run creates and drops its own database, leaving other databases untouched.
"""

import importlib.util
import contextlib
import json
import os
import subprocess
from pathlib import Path
import time
from unittest.mock import MagicMock
from unittest.mock import patch
import uuid

from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.agents.universal.drivers import pool as sdk_pool
from gcl_sdk.agents.universal.drivers import rawstor_specs
import psycopg
from psycopg import sql
import pytest
from restalchemy.common import contexts
from restalchemy.dm import filters as dm_filters
from restalchemy.storage.sql import engines

from exordos_core.compute.dm import models as compute
from exordos_core.storage import state
from exordos_core.storage.dm import models
from exordos_core.storage.scheduler import select
from exordos_core.user_api.storage.api import controllers

ROOT = Path(__file__).resolve().parents[4]
DSN = os.environ.get("EXORDOS_STORAGE_TEST_DB")
pytestmark = pytest.mark.skipif(
    not DSN, reason="Requires dedicated PostgreSQL test server"
)


def migration(filename):
    spec = importlib.util.spec_from_file_location(
        "storage_test_migration", ROOT / "migrations" / filename
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def storage_control_credentials(monkeypatch):
    """Keep storage SQL tests independent of certificate issuance and delivery."""
    from exordos_core.storage import tls
    from gcl_sdk.agents.universal.drivers import rawstor_tls

    @contextlib.contextmanager
    def bindings(_):
        import rawstor

        yield rawstor

    monkeypatch.setattr(tls, "credential", lambda *args: {})
    monkeypatch.setattr(tls, "bundle", lambda *args: {"service": {}, "control": {}})
    monkeypatch.setattr(tls, "prune_unused_certificates", lambda *args: None)
    monkeypatch.setattr(rawstor_tls, "temporary_bindings", bindings)


@pytest.fixture
def empty_database():
    name = "storage_test_" + uuid.uuid4().hex
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(
                sql.Identifier(name)
            )
        )
    uri = DSN.rsplit("/", 1)[0] + "/" + name
    try:
        engines.engine_factory.configure_factory(db_url=uri)
        yield uri
    finally:
        engines.engine_factory.destroy_engine()
        with psycopg.connect(DSN, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture
def database(empty_database):
    uri = empty_database
    import gcl_sdk.migrations as sdk_migrations
    from restalchemy.storage.sql import migrations

    engine = migrations.MigrationEngine(str(Path(sdk_migrations.__file__).parent))
    engine.apply_migration(engine.get_latest_migration())
    with psycopg.connect(uri) as connection:
        for statement in migration("0000-squashed-current-7f2e4a.py").SCHEMA_STATEMENTS:
            if statement.startswith("CREATE TABLE public.compute_machine_volumes"):
                connection.execute(statement)
                connection.execute(
                    "ALTER TABLE compute_machine_volumes ADD PRIMARY KEY(uuid)"
                )
        connection.execute(
            "ALTER TABLE compute_machine_volumes ADD tier varchar(16) DEFAULT 'WARM', ADD ephemeral boolean DEFAULT true, ADD storage_pool varchar(255)"
        )
        migration("0017-rawstor-storage-0b42fe.py").migration_step.upgrade(connection)
        migration("0018-managed-ost-compute-68ba8e2d.py").migration_step.upgrade(
            connection
        )
    return uri


def controller(cls):
    result = cls.__new__(cls)
    result._enforcer = MagicMock()
    result._enforcer.enforce.return_value = True
    result._ctx_project_id = None
    result._req = MagicMock()
    result._autovalues = {}
    result._autofilters = {}
    return result


@pytest.mark.parametrize("typed_spec", [False, True])
def test_clusters_nodes_pools_and_shared_pending_budget(database, typed_spec):
    cluster_api = controller(controllers.StorageClustersController)
    node_api = controller(controllers.StorageNodesController)
    pool_api = controller(controllers.StoragePoolsController)
    with contexts.Context().session_manager():
        cluster = cluster_api.create(
            name="cluster1",
            driver_spec={"kind": "rawstor", "endpoint": "mds://core:7776/"},
        )
        assert cluster.driver_spec.dump_to_simple_view() == {
            "kind": "rawstor",
            "endpoint": "mds://core:7776/",
        }
        assert {
            policy["name"] for policy in state.agent_driver_spec(cluster).pools.values()
        } == {"cluster1-persistent", "cluster1-ephemeral"}
        initial_target = models.Cluster.restore_from_simple_view(
            **cluster.dump_to_simple_view()
        ).to_ua_resource()
        assert "witness_endpoint" not in initial_target.value["driver_spec"]
        assert len(initial_target.value["driver_spec"]["pools"]) == 2
        assert initial_target.value["driver_spec"]["nodes"] == {}
        assert state.agent_driver_spec(cluster).nodes == {}
        agents = []
        for index in range(2):
            agent = ua_models.UniversalAgent(
                uuid=uuid.uuid4(),
                node=uuid.uuid4(),
                name=f"ost-agent{index}",
                capabilities={"capabilities": ["storage_node"]},
                facts={"facts": []},
            )
            agent.insert()
            agents.append(agent)
        first = node_api.create(
            name="ost1",
            agent=agents[0].uuid,
            cluster=cluster.uuid,
            driver_spec=(
                rawstor_specs.RawstorNodeDriverSpec(
                    endpoint="ost://10.0.0.1:7777",
                    failure_domain_path="dc1/row1/rack1/host1",
                )
                if typed_spec
                else {
                    "kind": "rawstor",
                    "endpoint": "ost://10.0.0.1:7777",
                    "failure_domain_path": "dc1/row1/rack1/host1",
                }
            ),
        )
        assert first.driver_spec.bind_address == "10.0.0.1:7777"
        second = node_api.create(
            name="ost2",
            driver_spec={
                "kind": "rawstor",
                "endpoint": "ost://10.0.0.2:7777",
                "failure_domain_path": "dc1/row1/rack2/host2",
            },
            agent=agents[1].uuid,
            cluster=cluster.uuid,
        )
        cluster = cluster_api.get(uuid=cluster.uuid)
        assert (
            state.agent_driver_spec(cluster).nodes == {}
        )  # Not admitted until the OST answers.
        for node in (first, second):
            node.status = "ACTIVE"
            node.update()
        state.sync_cluster(cluster)
        assert set(state.agent_driver_spec(cluster).nodes) == {
            str(first.uuid),
            str(second.uuid),
        }
        node_api.update(
            second.uuid,
            driver_spec={
                "kind": "rawstor",
                "failure_domain_path": "dc1/row1/rack3/host2",
            },
        )
        cluster = cluster_api.get(uuid=cluster.uuid)
        updated_node = node_api.get(uuid=second.uuid)
        assert updated_node.status == "IN_PROGRESS"
        assert str(second.uuid) not in state.agent_driver_spec(cluster).nodes
        updated_node.status = "ACTIVE"
        updated_node.update()
        state.sync_cluster(cluster)
        assert (
            state.agent_driver_spec(cluster).nodes[str(second.uuid)][
                "failure_domain_path"
            ]
            == "dc1/row1/rack3/host2"
        )
        ready_target = models.Cluster.restore_from_simple_view(
            **cluster.dump_to_simple_view()
        ).to_ua_resource()
        assert ready_target.hash != initial_target.hash
        assert (
            ready_target.value["driver_spec"]["nodes"][str(second.uuid)][
                "failure_domain_path"
            ]
            == "dc1/row1/rack3/host2"
        )
        reported = models.Cluster.from_ua_resource(ready_target)
        assert (
            reported.driver_spec.dump_to_simple_view()
            == cluster.driver_spec.dump_to_simple_view()
        )
        policies = models.StoragePool.objects.get_all()
        persistent = next(p for p in policies if not p.ephemeral)
        pool_api.update(
            persistent.uuid, driver_spec={"kind": "rawstor", "failure_domain": "rack"}
        )
        cluster = cluster_api.get(uuid=cluster.uuid)
        assert (
            state.agent_driver_spec(cluster).pools[str(persistent.uuid)][
                "failure_domain"
            ]
            == "rack"
        )
        updated_target = models.Cluster.restore_from_simple_view(
            **cluster.dump_to_simple_view()
        ).to_ua_resource()
        assert updated_target.hash != ready_target.hash
        assert (
            updated_target.value["driver_spec"]["pools"][str(persistent.uuid)][
                "failure_domain"
            ]
            == "rack"
        )
        inventory = [
            {
                "uuid": str(n.uuid),
                "failure_domain_path": n.driver_spec.failure_domain_path,
                "available": 100 << 30,
            }
            for n in (first, second)
        ]
        cluster.capacity_info = {
            "nodes": inventory,
            "objects": {},
            "reported_at": time.time(),
        }
        cluster.storage_pools = [
            sdk_pool.ThinStoragePool(
                uuid=p.uuid,
                name=p.name,
                pool_type="rawstor",
                tier=p.tier,
                ephemeral=p.ephemeral,
                driver_spec=rawstor_specs.RawstorPoolDriverSpec(
                    mirrors=p.driver_spec.mirrors,
                    failure_domain=p.driver_spec.failure_domain,
                ),
                capacity_usable=100,
            )
            for p in policies
        ]
        cluster.status = "ACTIVE"
        cluster.agent = uuid.uuid4()
        cluster.save()
        with patch.object(select, "_active_clusters", return_value=[cluster]):
            before = {
                p.ephemeral: p.available
                for p, _ in select.collect_storage_pool_candidates([])
            }
            assert before == {False: 100, True: 200}
            staged = {str(cluster.uuid): {str(uuid.uuid4()): 20 << 30}}
            reserved = {
                p.ephemeral: p.available
                for p, _ in select.collect_storage_pool_candidates([], staged)
            }
            assert reserved == {False: 80, True: 160}
            cluster.capacity_info["reported_at"] = time.time() - 61
            assert select.collect_storage_pool_candidates([]) == []
            cluster.capacity_info["reported_at"] = time.time()
            volume = compute.MachineVolume(
                uuid=uuid.uuid4(),
                project_id=uuid.uuid4(),
                name="v",
                size=20,
                storage_pool=str(persistent.uuid),
                storage_backend="rawstor",
            )
            volume.insert()
            after = {
                p.ephemeral: p.available
                for p, _ in select.collect_storage_pool_candidates([])
            }
            assert after == {False: 80, True: 160}
            from restalchemy.storage import exceptions

            with pytest.raises(exceptions.ConflictRecords):
                pool_api.delete(persistent.uuid)
            with pytest.raises(exceptions.ConflictRecords):
                node_api.delete(first.uuid)
            # Agent observes the object: pending admission no longer charges it twice.
            cluster.capacity_info["objects"][str(volume.uuid)] = 20 << 30
            for node in inventory:
                node["available"] = 80 << 30
            observed = {
                p.ephemeral: p.available
                for p, _ in select.collect_storage_pool_candidates([])
            }
            assert observed == after
            volume.delete()
        from restalchemy.storage import exceptions

        with patch.dict(
            "sys.modules",
            rawstor=MagicMock(
                Location=MagicMock(
                    side_effect=[
                        MagicMock(objects=MagicMock(return_value=[])),
                        MagicMock(objects=MagicMock(return_value=[object()])),
                    ]
                )
            ),
        ):
            with pytest.raises(exceptions.ConflictRecords):
                cluster_api.delete(cluster.uuid)
        assert len(models.StorageNode.objects.get_all()) == 2
        assert cluster_api.get(uuid=cluster.uuid).status != "DELETING"
        with patch.dict(
            "sys.modules",
            rawstor=MagicMock(
                Location=MagicMock(
                    return_value=MagicMock(objects=MagicMock(return_value=[]))
                )
            ),
        ):
            cluster_api.delete(cluster.uuid)
        assert models.StorageNode.objects.get_all() == []
        assert models.StoragePool.objects.get_all() == []
        assert models.StorageCluster.objects.get_all() == []


def test_shared_budget_lock_blocks_another_transaction(database):
    import concurrent.futures
    import threading

    entered = threading.Event()

    def reserve():
        with psycopg.connect(database) as conn:
            entered.set()
            state.lock(conn)
        return True

    with psycopg.connect(database) as owner:
        state.lock(owner)
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(reserve)
            assert entered.wait(5)
            with pytest.raises(concurrent.futures.TimeoutError):
                future.result(timeout=0.1)
            owner.commit()
            assert future.result(timeout=5)


def test_storage_schema_upgrade_and_downgrade_preserve_local_volumes(database):
    volume_uuid = uuid.uuid4()
    with contexts.Context().session_manager():
        compute.MachineVolume(
            uuid=volume_uuid,
            project_id=uuid.uuid4(),
            name="local",
            size=10,
            storage_pool="default",
        ).insert()
    with psycopg.connect(database) as connection:
        step = migration("0017-rawstor-storage-0b42fe.py").migration_step
        step.downgrade(connection)
        for table in ("storage_clusters", "storage_nodes", "storage_pools"):
            assert connection.execute(
                "SELECT to_regclass(%s)", (table,)
            ).fetchone() == (None,)
        connection.execute(
            "UPDATE compute_machine_volumes SET updated_at='2000-01-01' WHERE uuid=%s",
            (volume_uuid,),
        )
        step.upgrade(connection)
        assert connection.execute(
            "SELECT updated_at > '2000-01-01', storage_backend FROM compute_machine_volumes WHERE uuid=%s",
            (volume_uuid,),
        ).fetchone() == (True, "local")
        cluster_uuid = uuid.uuid4()
        connection.execute(
            "INSERT INTO storage_clusters (uuid,name,description,driver_spec,status) "
            "VALUES (%s,'cluster','',%s::jsonb,'DISABLED')",
            (
                cluster_uuid,
                json.dumps({"kind": "rawstor", "endpoint": "mds://core:7776/"}),
            ),
        )
        node_uuid = uuid.uuid4()
        connection.execute(
            "INSERT INTO storage_nodes (uuid,name,cluster,driver_spec) "
            "VALUES (%s,'ost',%s,%s::jsonb)",
            (
                node_uuid,
                cluster_uuid,
                json.dumps({"kind": "rawstor", "endpoint": "ost://host:7777"}),
            ),
        )
        assert connection.execute(
            "SELECT agent,builder,status FROM storage_nodes"
        ).fetchone() == (None, None, "NEW")
        assert connection.execute(
            "SELECT capacity_info,storage_pools,nodes,size FROM storage_clusters"
        ).fetchone() == ({}, [], 0, 10)
        step.downgrade(connection)
        assert connection.execute(
            "SELECT storage_pool FROM compute_machine_volumes WHERE uuid=%s",
            (volume_uuid,),
        ).fetchone() == ("default",)


@pytest.mark.parametrize(
    "previous_head",
    [
        None,
        "0004-add-opaque-secrets-362f6d21.py",
        "0013-realm-scoped-dns-5bd99b.py",
    ],
)
def test_full_migration_graph_applies_from_empty_database(
    empty_database, previous_head
):
    import gcl_sdk.migrations as sdk_migrations
    from restalchemy.storage.sql import migrations

    for path in (Path(sdk_migrations.__file__).parent, ROOT / "migrations"):
        engine = migrations.MigrationEngine(str(path))
        if path == ROOT / "migrations" and previous_head:
            engine.apply_migration(previous_head)
        engine.apply_migration(engine.get_latest_migration())
    with psycopg.connect(empty_database) as connection:
        tables = {
            r[0]
            for r in connection.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public'"
            )
        }
        assert {
            "storage_clusters",
            "storage_nodes",
            "storage_pools",
            "secret_secrets",
            "storage_secrets",
        } <= tables


def test_managed_ost_registration_settings_and_ordered_cluster_shutdown(
    database, tmp_path, monkeypatch
):
    from gcl_sdk.agents.universal.drivers import rawstor_node

    monkeypatch.setattr(
        controllers.storage_capacity,
        "OST_BACKING_ROOTS",
        (*controllers.storage_capacity.OST_BACKING_ROOTS, str(tmp_path)),
    )
    monkeypatch.setattr(
        rawstor_node,
        "_prepare_file_backing",
        lambda path: Path(path).mkdir(exist_ok=True),
    )

    from exordos_core.storage.builders import cluster as cluster_builder
    from gcl_sdk.agents.universal.drivers import rawstor_tls

    monkeypatch.setattr(rawstor_tls, "install", lambda *args: (tmp_path / "tls", False))
    monkeypatch.setattr(rawstor_tls, "service_environment", lambda _: "")
    monkeypatch.setattr(rawstor_tls, "reload_if_changed", lambda *args: None)
    monkeypatch.setattr(rawstor_tls, "remove", lambda *args: None)

    cluster_api = controller(controllers.StorageClustersController)
    node_api = controller(controllers.StorageNodesController)
    service = cluster_builder.StorageClusterBuilderService.__new__(
        cluster_builder.StorageClusterBuilderService
    )
    service._instance_model = models.Node
    driver = rawstor_node.StorageNodeAgentDriver(meta_file=str(tmp_path / "meta.json"))
    driver.start()
    with (
        contexts.Context().session_manager(),
        patch.object(rawstor_node, "OST_UNIT_DIR", tmp_path / "units"),
        patch.object(rawstor_node, "OST_CONFIG_DIR", tmp_path / "config"),
    ):
        cluster = cluster_api.create(
            name="cluster",
            driver_spec={"kind": "rawstor", "endpoint": "mds://core:7776/"},
        )
        agent = ua_models.UniversalAgent(
            uuid=uuid.uuid4(),
            node=uuid.uuid4(),
            name="storage-agent",
            capabilities={"capabilities": ["storage_node"]},
            facts={"facts": []},
        )
        agent.insert()
        node = node_api.create(
            name="ost",
            cluster=cluster.uuid,
            agent=agent.uuid,
            driver_spec={
                "kind": "rawstor",
                "endpoint": "ost://10.0.0.1:7777",
                "location": f"file://{tmp_path}/data",
                "failure_domain_path": "dc/row/rack/server",
            },
        )
        assert node.status == "NEW"
        assert state.agent_driver_spec(cluster_api.get(uuid=cluster.uuid)).nodes == {}
        current = models.Node.restore_from_simple_view(**node.dump_to_simple_view())
        desired = current.to_ua_resource()
        desired.agent = agent.uuid
        desired.insert()
        assert desired.value["driver_spec"]["mds"] == cluster.driver_spec.endpoint
        with (
            patch.object(
                rawstor_node.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0),
            ),
            patch("rawstor.Location"),
        ):
            actual = driver.create(desired)
        assert actual.hash == desired.hash  # Real CP/DP serialization contract.
        assert actual.status == "ACTIVE"
        actual.agent = agent.uuid
        actual.insert()
        driver.finalize()
        service.actualize_outdated_instance(
            current, models.Node.from_ua_resource(actual)
        )
        cluster = cluster_api.get(uuid=cluster.uuid)
        assert str(node.uuid) in state.agent_driver_spec(cluster).nodes
        mds_target = models.Cluster.restore_from_simple_view(
            **cluster.dump_to_simple_view()
        ).to_ua_resource()
        mds_target.insert()
        mds_actual = ua_models.Resource(
            uuid=cluster.uuid,
            kind="storage_cluster",
            res_uuid=mds_target.res_uuid,
            hash=mds_target.hash,
            status="ACTIVE",
            value=mds_target.value,
        )
        mds_actual.insert()
        # The API removes desired membership, while the OST stays alive.
        with patch("rawstor.Location") as location:
            location.return_value.objects.return_value = iter([])
            node_api.delete(node.uuid)
        cluster = cluster_api.get(uuid=cluster.uuid)
        removed = models.Cluster.restore_from_simple_view(
            **cluster.dump_to_simple_view()
        ).to_ua_resource()
        mds_target.value = removed.value
        mds_target.hash = removed.hash
        mds_target.update()
        assert service.can_delete_instance_resource(desired)
        mds_actual.value = removed.value
        mds_actual.hash = removed.hash
        mds_actual.update()
        assert service.can_delete_instance_resource(desired)
        desired.delete()  # Host payload no longer contains the OST.
        from restalchemy.storage import exceptions

        with pytest.raises(exceptions.ConflictRecords):
            node_api.create(
                name="replacement",
                cluster=cluster.uuid,
                agent=agent.uuid,
                driver_spec={
                    "kind": "rawstor",
                    "endpoint": node.driver_spec.endpoint,
                    "failure_domain_path": "server",
                },
            )
        cluster_api.delete(cluster.uuid)  # Accepted while the host is still stopping.
        cluster = cluster_api.get(uuid=cluster.uuid)
        assert cluster.status == "DELETING"
        assert not state.finish_cluster_deletion(cluster)
        service.actualize_outdated_instance(
            models.Cluster.restore_from_simple_view(**cluster.dump_to_simple_view()),
            models.Cluster.from_ua_resource(mds_actual),
        )
        assert cluster_api.get(uuid=cluster.uuid).status == "DELETING"
        backing = tmp_path / "data"
        backing.mkdir(exist_ok=True)
        (backing / "keep").write_text("data")
        with (
            patch.object(rawstor_node.subprocess, "run") as run,
            patch("rawstor.Location"),
        ):
            driver.delete(desired)
        assert [
            "systemctl",
            "disable",
            "--now",
            f"rawstor-ost@{node.uuid}.service",
        ] in [c.args[0] for c in run.call_args_list]
        assert not (tmp_path / "units" / f"rawstor-ost@{node.uuid}.service").exists()
        assert (backing / "keep").read_text() == "data"
        actual.delete()  # Host acknowledges deletion through the status API.
        with pytest.raises(exceptions.ConflictRecords):
            node_api.create(
                name="replacement",
                cluster=cluster.uuid,
                agent=agent.uuid,
                driver_spec={
                    "kind": "rawstor",
                    "endpoint": node.driver_spec.endpoint,
                    "failure_domain_path": "server",
                },
            )
        assert state.finish_cluster_deletion(cluster_api.get(uuid=cluster.uuid))
        assert models.StorageCluster.objects.get_all() == []
        driver.finalize()


def test_cluster_metadata_update_does_not_request_an_mds_reconcile(database):
    cluster_api = controller(controllers.StorageClustersController)
    with contexts.Context().session_manager():
        cluster = cluster_api.create(
            name="cluster",
            driver_spec={"kind": "rawstor", "endpoint": "mds://core:7776/"},
        )
        target = models.Cluster.restore_from_simple_view(
            **cluster.dump_to_simple_view()
        ).to_ua_resource()
        target.insert()
        cluster.status = "ACTIVE"
        cluster.update()
        updated = cluster_api.update(cluster.uuid, description="updated description")
        assert updated.status == "ACTIVE"
        assert updated.driver_spec.dump_to_simple_view() == {
            "kind": "rawstor",
            "endpoint": "mds://core:7776/",
        }


def test_default_names_and_partial_cluster_spec(database):
    cluster_api = controller(controllers.StorageClustersController)
    pool_api = controller(controllers.StoragePoolsController)
    with contexts.Context().session_manager():
        cluster = cluster_api.create(
            driver_spec={"kind": "rawstor", "endpoint": "mds://core:7776/"}
        )
        assert cluster.name == ""
        updated = cluster_api.update(
            cluster.uuid, driver_spec={"endpoint": "mds://core:7776/"}
        )
        assert updated.driver_spec.KIND == "rawstor"
        created = pool_api.create(
            cluster=cluster.uuid,
            tier="HOT",
            ephemeral=False,
            driver_spec={
                "kind": "rawstor",
                "mirrors": 2,
                "failure_domain": "server",
            },
        )
        assert created.name == ""
        long_cluster = cluster_api.create(
            name="a" * 255,
            driver_spec={"kind": "rawstor", "endpoint": "mds://core:7778/"},
        )
        assert all(
            len(p.name) <= 255
            for p in models.StoragePool.objects.get_all(
                filters={"cluster": dm_filters.EQ(long_cluster.uuid)}
            )
        )


def test_ready_ost_membership_survives_a_capacity_report(database):
    from exordos_core.storage.builders.cluster import StorageClusterBuilderService

    with contexts.Context().session_manager():
        cluster = controller(controllers.StorageClustersController).create(
            name="storage",
            driver_spec={"kind": "rawstor", "endpoint": "mds://core:7776/"},
        )
        current = models.Cluster.restore_from_simple_view(
            **cluster.dump_to_simple_view()
        )
        target = current.to_ua_resource()
        target.insert()
        actual = ua_models.Resource(
            uuid=target.uuid,
            kind=target.kind,
            hash=target.hash,
            value={**target.value, "status": "ACTIVE"},
        )
        actual.insert()
        node = models.StorageNode(
            cluster=cluster.uuid,
            name="ready",
            status="ACTIVE",
            driver_spec=rawstor_specs.RawstorNodeDriverSpec(
                endpoint="ost://10.0.0.1:7777", failure_domain_path="server"
            ),
        )
        node.insert()
        state.sync_cluster(cluster)
        target = ua_models.TargetResource.objects.get_one(
            filters={"uuid": dm_filters.EQ(cluster.uuid)}
        )
        assert str(node.uuid) in target.value["driver_spec"]["nodes"]
        assert target.hash != actual.hash
        service = StorageClusterBuilderService.__new__(StorageClusterBuilderService)
        service._instance_model = models.Cluster
        current = models.Cluster.objects.get_one(
            filters={"uuid": dm_filters.EQ(cluster.uuid)}
        )
        service._actualize_outdated_instance(current, target, actual)
        target = ua_models.TargetResource.objects.get_one(
            filters={"uuid": dm_filters.EQ(cluster.uuid)}
        )
        assert str(node.uuid) in target.value["driver_spec"]["nodes"]
        assert target.hash != actual.hash
        assert current.status == "IN_PROGRESS"
        # MDS applies the new target and the cluster can finally converge.
        actual.value = {**target.value, "status": "ACTIVE"}
        actual.hash = target.hash
        actual.update()
        service._actualize_outdated_instance(current, target, actual)
        assert current.status == "ACTIVE"


def test_managed_ost_vm_creation_and_drained_cleanup_use_real_models(empty_database):
    import gcl_sdk.migrations as sdk_migrations
    from restalchemy.storage.sql import migrations

    from exordos_core.common import constants as cc
    from exordos_core.storage import managed
    from exordos_core.vs.dm import models as vs

    sdk_engine = migrations.MigrationEngine(str(Path(sdk_migrations.__file__).parent))
    sdk_engine.apply_migration(sdk_engine.get_latest_migration())
    engine = migrations.MigrationEngine(str(ROOT / "migrations"))
    engine.apply_migration(engine.get_latest_migration())
    with contexts.Context().session_manager():
        vs.Variable(
            uuid=managed.IMAGE_VARIABLE,
            project_id=cc.EM_PROJECT_ID,
            name="rawstor_ost_image",
            setter=vs.SelectorVariableSetter(),
            value="urn:images:018ddff6-52cf-4cb9-ae73-889f47b02bb5",
        ).insert()
        cluster = models.StorageCluster(
            name="managed",
            nodes=2,
            size=30,
            cpu=4,
            ram=4096,
            driver_spec=sdk_pool.RawstorStorageClusterDriverSpec(
                endpoint="mds://core:7776/"
            ),
        )
        cluster.insert()
        with models.StorageCluster._get_engine().session_manager() as session:
            with pytest.raises(ValueError, match="Delete managed OST clusters"):
                migration("0017-rawstor-storage-0b42fe.py").migration_step.downgrade(
                    session
                )
        managed.reconcile(cluster)
        nodes = compute.Node.objects.get_all()
        assert len(nodes) == 2
        assert all(node.cores == 4 and node.ram == 4096 for node in nodes)
        assert len(compute.Volume.objects.get_all()) == 4
        assert len(ua_models.NodeEncryptionKey.objects.get_all()) == 2
        assert not models.StorageNode.objects.get_all()
        for index, node in enumerate(nodes):
            node.default_network = {"ipv4": f"10.100.0.{20 + index}"}
            node.update()
            ua_models.UniversalAgent(
                uuid=node.uuid,
                node=node.uuid,
                name=node.name,
                status="ACTIVE",
                capabilities={"capabilities": ["guest_machine", "storage_node"]},
            ).insert()
        managed.reconcile(cluster)
        managed.reconcile(cluster)
        osts = models.StorageNode.objects.get_all()
        assert len(osts) == 2
        assert all(
            ost.driver_spec.location == f"lvm://rawstor-{ost.uuid}" for ost in osts
        )
        assert not state.finish_cluster_deletion(cluster)
        assert len(compute.Node.objects.get_all()) == 2
        for ost in osts:
            ost.delete()
        assert state.finish_cluster_deletion(cluster)
        assert not compute.Node.objects.get_all()
        assert not compute.Volume.objects.get_all()


def test_cluster_error_status_roundtrips(database):
    with contexts.Context().session_manager():
        cluster = models.StorageCluster(
            name="failed",
            driver_spec=sdk_pool.RawstorStorageClusterDriverSpec(
                endpoint="mds://core:7776/"
            ),
        )
        cluster.insert()
        cluster.status = "ERROR"
        cluster.update()
    with contexts.Context().session_manager():
        assert (
            models.StorageCluster.objects.get_one(
                filters={"uuid": dm_filters.EQ(cluster.uuid)}
            ).status
            == "ERROR"
        )


def test_idle_assignment_savepoint_rolls_back_partial_writes(database, monkeypatch):
    from types import SimpleNamespace
    from exordos_core.compute.scheduler import service
    from exordos_core.compute.scheduler.driver import base

    scheduler = service.SchedulerService([], [], [], [])
    local = sdk_pool.ThinStoragePool(name="local", pool_type="dir", capacity_usable=10)
    pool = base.MachinePoolBundle(
        pool=compute.MachinePool(
            name="pool",
            storage_pools=[local],
            driver_spec=sdk_pool.ExordosLocalHyperDriverSpec(
                connection_uri="test:///default", node=uuid.uuid4()
            ),
        ),
        volumes=[],
    )
    with contexts.Context().session_manager() as session:
        session.execute("CREATE TABLE idle_assignment_probe (uuid uuid PRIMARY KEY)")
        nodes = []
        for _ in range(2):
            identifier = uuid.uuid4()
            node = SimpleNamespace(
                uuid=identifier, node_type="VM", status="NEW", target_power_state="ON"
            )
            node.save = lambda identifier=identifier: session.execute(
                "INSERT INTO idle_assignment_probe VALUES (%s)", (str(identifier),)
            )
            nodes.append(
                base.NodeBundle(
                    node,
                    [
                        compute.Volume(
                            uuid=uuid.uuid4(),
                            project_id=uuid.uuid4(),
                            size=6,
                        )
                    ],
                )
            )
        machine = SimpleNamespace(
            uuid=uuid.uuid4(),
            pool=pool.pool.uuid,
            machine_type="VM",
            node=None,
            status="ACTIVE",
            target_power_state="OFF",
        )
        attempts = []

        def save_machine():
            session.execute(
                "INSERT INTO idle_assignment_probe VALUES (%s)", (str(machine.uuid),)
            )
            attempts.append(None)
            if len(attempts) == 1:
                raise RuntimeError("machine persistence failed")

        machine.save = save_machine
        monkeypatch.setattr(scheduler, "_get_unscheduled_nodes", lambda: nodes)
        monkeypatch.setattr(
            scheduler, "_get_idle_machines", lambda: [base.MachineBundle(machine, [])]
        )
        monkeypatch.setattr(select, "_active_clusters", lambda: [])
        scheduler._schedule_on_existing_machines([pool])
        with session.execute("SELECT uuid FROM idle_assignment_probe") as cursor:
            persisted = {str(row["uuid"]) for row in cursor.fetchall()}
        assert persisted == {str(nodes[1].node.uuid), str(machine.uuid)}
        assert nodes[0].node.status == "NEW"
        assert local.capacity_provisioned == 6


@pytest.mark.parametrize("failed_save", ["node", "machine", "volume"])
def test_new_vm_assignment_savepoint_rolls_back_partial_writes(
    database, monkeypatch, failed_save
):
    from types import SimpleNamespace
    from exordos_core.compute.scheduler import service
    from exordos_core.compute.scheduler.driver import base

    scheduler = service.SchedulerService([], [], [], [])
    local = sdk_pool.ThinStoragePool(name="local", pool_type="dir", capacity_usable=10)
    pool = base.MachinePoolBundle(
        pool=compute.MachinePool(
            name="pool",
            storage_pools=[local],
            avail_cores=8,
            avail_ram=8192,
            driver_spec=sdk_pool.ExordosLocalHyperDriverSpec(
                connection_uri="test:///default", node=uuid.uuid4()
            ),
        ),
        volumes=[],
    )
    monkeypatch.setattr(select, "_active_clusters", lambda: [])
    with contexts.Context().session_manager() as session:
        session.execute(
            "CREATE TABLE new_vm_assignment_probe (kind text, uuid uuid, PRIMARY KEY(kind, uuid))"
        )
        fail = [True]
        volume_writes = []

        def save(kind, identifier):
            session.execute(
                "INSERT INTO new_vm_assignment_probe VALUES (%s, %s)",
                (kind, str(identifier)),
            )
            if kind == "volume":
                volume_writes.append(identifier)
            if (
                fail[0]
                and kind == failed_save
                and (kind != "volume" or len(volume_writes) == 2)
            ):
                raise RuntimeError("persistence failed")

        node = SimpleNamespace(
            uuid=uuid.uuid4(),
            name="vm",
            cores=1,
            ram=1024,
            project_id=uuid.uuid4(),
            target_power_state="ON",
            pool=None,
        )
        node.save = lambda: save("node", node.uuid)
        monkeypatch.setattr(
            compute.Machine, "save", lambda self: save("machine", self.uuid)
        )
        monkeypatch.setattr(
            compute.MachineVolume, "save", lambda self: save("volume", self.uuid)
        )
        bundle = base.NodeBundle(
            node,
            [
                compute.Volume(uuid=uuid.uuid4(), project_id=node.project_id, size=size)
                for size in (6, 1)
            ],
        )

        with pytest.raises(RuntimeError, match="persistence failed"):
            scheduler._place_node_into_pool(bundle, pool)
        assert session.execute("SELECT * FROM new_vm_assignment_probe").fetchall() == []
        assert node.pool is None and local.capacity_provisioned == 0
        assert pool.pool.avail_cores == 8 and pool.pool.avail_ram == 8192

        fail[0] = False
        scheduler._place_node_into_pool(bundle, pool)
        with session.execute("SELECT * FROM new_vm_assignment_probe") as cursor:
            assert len(cursor.fetchall()) == 4
        assert local.capacity_provisioned == 7
        assert pool.pool.avail_cores == 7 and pool.pool.avail_ram == 7168


def test_object_jobs_wait_for_hypervisor_detach_acknowledgement(database):
    from exordos_core.storage import volumes

    with contexts.Context().session_manager():
        cluster = controller(controllers.StorageClustersController).create(
            name="object-owner",
            driver_spec={"kind": "rawstor", "endpoint": "mds://core:7776/"},
        )
        agent = ua_models.UniversalAgent(
            uuid=uuid.uuid4(),
            node=uuid.uuid4(),
            name="core-object-agent",
            capabilities={"capabilities": ["storage_cluster", "storage_volume"]},
            facts={"facts": []},
        )
        agent.insert()
        cluster.agent = agent.uuid
        cluster.builder = uuid.uuid4()
        cluster.save()
        policy = models.StoragePool.objects.get_all(filters={"cluster": cluster.uuid})[
            0
        ]
        disk = compute.MachineVolume(
            uuid=uuid.uuid4(),
            project_id=uuid.uuid4(),
            name="object",
            size=8,
            storage_pool=str(policy.uuid),
            storage_backend="rawstor",
        )
        disk.insert()
        volumes.sync_targets(cluster.builder)
        job = ua_models.TargetResource.objects.get_one(
            filters={"uuid": disk.uuid, "kind": "storage_volume"}
        )
        assert job.agent == cluster.agent
        disk.size = 16
        disk.update()
        volumes.sync_targets(cluster.builder)
        assert (
            ua_models.TargetResource.objects.get_one(
                filters={"uuid": disk.uuid, "kind": "storage_volume"}
            ).value["size"]
            == 16
        )
        actual = ua_models.Resource(
            uuid=disk.uuid,
            kind="pool_volume",
            value={},
            agent=agent.uuid,
            res_uuid=ua_models.Resource.gen_res_uuid(disk.uuid, "pool_volume"),
        )
        actual.insert()
        disk.delete()
        volumes.sync_targets(cluster.builder)
        assert ua_models.TargetResource.objects.get_all(
            filters={"uuid": disk.uuid, "kind": "storage_volume"}
        )
        actual.delete()
        volumes.sync_targets(cluster.builder)
        assert not ua_models.TargetResource.objects.get_all(
            filters={"uuid": disk.uuid, "kind": "storage_volume"}
        )


def test_managed_compute_schema_upgrades_existing_clusters(database):
    identifier = uuid.uuid4()
    step = migration("0018-managed-ost-compute-68ba8e2d.py").migration_step
    with psycopg.connect(database) as connection:
        step.downgrade(connection)
        connection.execute(
            "INSERT INTO storage_clusters (uuid,name,description,driver_spec,status) "
            "VALUES (%s,'existing','',%s::jsonb,'DISABLED')",
            (
                identifier,
                json.dumps({"kind": "rawstor", "endpoint": "mds://core:7776/"}),
            ),
        )
        step.upgrade(connection)
        assert connection.execute(
            "SELECT cpu, ram FROM storage_clusters WHERE uuid=%s", (identifier,)
        ).fetchone() == (2, 2048)
    with contexts.Context().session_manager():
        existing = models.StorageCluster.objects.get_one(
            filters={"uuid": dm_filters.EQ(identifier)}
        )
        assert (existing.cpu, existing.ram) == (2, 2048)
        custom = models.StorageCluster(
            name="custom",
            nodes=2,
            cpu=4,
            ram=4096,
            driver_spec=sdk_pool.RawstorStorageClusterDriverSpec(
                endpoint="mds://core:7777/"
            ),
        )
        custom.insert()
    with contexts.Context().session_manager():
        stored = models.StorageCluster.objects.get_one(
            filters={"uuid": dm_filters.EQ(custom.uuid)}
        )
        assert (stored.cpu, stored.ram) == (4, 4096)
