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
import json
import os
from pathlib import Path
import time
from unittest.mock import MagicMock
from unittest.mock import patch
import uuid

from gcl_sdk.agents.universal.drivers import pool as sdk_pool
import psycopg
from psycopg import sql
import pytest
from restalchemy.common import contexts
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


@pytest.fixture
def database():
    name = "storage_test_" + uuid.uuid4().hex
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(
                sql.Identifier(name)
            )
        )
    uri = DSN.rsplit("/", 1)[0] + "/" + name
    try:
        with psycopg.connect(uri) as connection:
            for statement in migration(
                "0000-squashed-current-7f2e4a.py"
            ).SCHEMA_STATEMENTS:
                if statement.startswith("CREATE TABLE public.compute_machine_volumes"):
                    connection.execute(statement)
                    connection.execute(
                        "ALTER TABLE compute_machine_volumes ADD PRIMARY KEY(uuid)"
                    )
            connection.execute(
                "ALTER TABLE compute_machine_volumes ADD speed varchar(16) DEFAULT 'HOT', ADD ephemeral boolean DEFAULT true, ADD storage_pool varchar(255)"
            )
            migration("0004-storage-clusters-3d8a1c.py").migration_step.upgrade(
                connection
            )
            migration("0005-storage-nodes-pools-8ce6d2.py").migration_step.upgrade(
                connection
            )
        engines.engine_factory.configure_factory(db_url=uri)
        yield uri
    finally:
        engines.engine_factory.destroy_engine()
        with psycopg.connect(DSN, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )


def controller(cls):
    result = cls.__new__(cls)
    result._enforcer = MagicMock()
    result._enforcer.enforce.return_value = True
    result._ctx_project_id = None
    result._req = MagicMock()
    result._autovalues = {}
    result._autofilters = {}
    return result


def test_clusters_nodes_pools_and_shared_pending_budget(database):
    cluster_api = controller(controllers.StorageClustersController)
    node_api = controller(controllers.StorageNodesController)
    pool_api = controller(controllers.StoragePoolsController)
    with contexts.Context().session_manager():
        cluster = cluster_api.create(
            name="cluster1",
            driver_spec={"kind": "rawstor", "endpoint": "mds://core:7776/"},
        )
        assert len(cluster.driver_spec.pools) == 2
        assert cluster.driver_spec.nodes == {}
        first = node_api.create(
            name="ost1",
            cluster=cluster.uuid,
            endpoint="ost://host1:7777",
            failure_domain_path="dc1/row1/rack1/host1",
        )
        second = node_api.create(
            name="ost2",
            cluster=cluster.uuid,
            endpoint="ost://host2:7777",
            failure_domain_path="dc1/row1/rack2/host2",
        )
        cluster = cluster_api.get(uuid=cluster.uuid)
        assert set(cluster.driver_spec.nodes) == {str(first.uuid), str(second.uuid)}
        node_api.update(second.uuid, weight=2.0)
        cluster = cluster_api.get(uuid=cluster.uuid)
        assert cluster.driver_spec.nodes[str(second.uuid)]["weight"] == 2.0
        policies = models.StoragePool.objects.get_all()
        persistent = next(p for p in policies if not p.ephemeral)
        pool_api.update(persistent.uuid, failure_domain="rack")
        cluster = cluster_api.get(uuid=cluster.uuid)
        assert (
            cluster.driver_spec.pools[str(persistent.uuid)]["failure_domain"] == "rack"
        )
        inventory = [
            {
                "uuid": str(n.uuid),
                "failure_domain_path": n.failure_domain_path,
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
                speed=p.speed,
                ephemeral=p.ephemeral,
                mirrors=p.mirrors,
                failure_domain=p.failure_domain,
                chunk_size=p.chunk_size,
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
                storage_location=cluster.driver_spec.endpoint,
                storage_policy={
                    "pool_uuid": str(persistent.uuid),
                    "mirrors": 2,
                    "chunk_size": 1 << 30,
                    "failure_domain": "rack",
                },
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

        with pytest.raises(exceptions.ConflictRecords):
            cluster_api.delete(cluster.uuid)
        with patch("rawstor.Location", return_value=[]):
            node_api.delete(first.uuid)
            node_api.delete(second.uuid)
        cluster_api.delete(cluster.uuid)
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


def test_migration_preserves_legacy_ost_pool_and_disk_policy(database):
    cluster_uuid, pool_uuid, volume_uuid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    endpoint = "mds://core:7788/"
    with contexts.Context().session_manager():
        compute.MachineVolume(
            uuid=volume_uuid,
            project_id=uuid.uuid4(),
            name="legacy",
            size=10,
            storage_location=endpoint,
            storage_pool="default",
        ).insert()
    with psycopg.connect(database) as connection:
        step = migration("0005-storage-nodes-pools-8ce6d2.py").migration_step
        step.downgrade(connection)
        spec = {
            "kind": "rawstor",
            "endpoint": endpoint,
            "location": "file:///data/rawstor",
            "ost_endpoint": "ost://host:7777",
            "speed": "HOT",
            "ephemeral": False,
        }
        policy = {
            "uuid": str(pool_uuid),
            "name": "default",
            "speed": "HOT",
            "ephemeral": False,
        }
        connection.execute(
            """INSERT INTO storage_clusters(uuid,name,description,driver_spec,status,storage_pools)
            VALUES (%s,'legacy','',%s::jsonb,'ACTIVE',ARRAY[%s::jsonb])""",
            (cluster_uuid, json.dumps(spec), json.dumps(policy)),
        )
        step.upgrade(connection)
        assert connection.execute(
            "SELECT uuid,endpoint,failure_domain_path FROM storage_nodes"
        ).fetchone() == (cluster_uuid, "ost://host:7777", str(cluster_uuid))
        assert connection.execute(
            "SELECT uuid,mirrors FROM storage_pools"
        ).fetchone() == (pool_uuid, 1)
        recorded = connection.execute(
            "SELECT storage_policy FROM compute_machine_volumes WHERE uuid=%s",
            (volume_uuid,),
        ).fetchone()[0]
        assert recorded == {
            "pool_uuid": str(pool_uuid),
            "mirrors": 1,
            "chunk_size": 1 << 30,
            "failure_domain": "server",
        }
