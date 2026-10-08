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

import importlib.util
import json
import os
from pathlib import Path
import uuid

from gcl_sdk.infra import constants as ic
import psycopg
from psycopg.rows import dict_row
import pytest

PATH = Path(__file__).parents[4] / "migrations/0004-disk-scheduling-resources-96f43b.py"
spec = importlib.util.spec_from_file_location("disk_scheduling_migration", PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def test_enrich_preserves_explicit_policy_and_adds_nested_fields():
    value = {
        "disk_spec": {
            "kind": "disks",
            "disks": [
                {"size": 10},
                {"size": 20, "speed": ic.DiskSpeed.HOT.value, "ephemeral": True},
            ],
        },
        "storage_pools": [{"name": "default", "available_actual": 100}],
    }
    result = migration.enrich(value)
    assert result["disk_spec"]["disks"][0] == {
        "size": 10,
        "speed": ic.DiskSpeed.WARM.value,
        "ephemeral": False,
    }
    assert result["disk_spec"]["disks"][1] == value["disk_spec"]["disks"][1]
    assert result["storage_pools"][0]["available_actual"] == 100
    assert "speed" not in value["disk_spec"]["disks"][0]
    assert migration.enrich(result) == result


def test_enrich_volume_and_target_fields():
    value = {"size": 10, "storage_pool": "existing", "target_fields": {"size": None}}
    result = migration.enrich(value, volume=True)
    assert result["storage_pool"] == "existing"
    assert result["ephemeral"] is False
    assert result["target_fields"]["speed"] is None
    assert "storage_pool" in result["target_fields"]


@pytest.mark.skipif(
    not os.environ.get("EXORDOS_STORAGE_TEST_DB"), reason="Requires test PostgreSQL"
)
def test_migration_updates_target_actual_and_persisted_sources():
    class Session:
        def execute(self, statement, values=None):
            return connection.execute(statement, values)

    with psycopg.connect(
        os.environ["EXORDOS_STORAGE_TEST_DB"],
        row_factory=dict_row,
        options="-c client_encoding=UTF8",
    ) as connection:
        for table, column in (
            ("nodes", "disk_spec"),
            ("compute_sets", "disk_spec"),
            ("machine_pools", "storage_pools"),
        ):
            connection.execute(
                f"CREATE TEMP TABLE {table} (uuid uuid, {column} jsonb, updated_at timestamptz)"
            )
            value = (
                [{"name": "default"}]
                if column == "storage_pools"
                else {"kind": "root_disk", "size": 10}
            )
            connection.execute(
                f"INSERT INTO {table} (uuid, {column}) VALUES (%s, %s)",
                (uuid.uuid4(), json.dumps(value)),
            )
        for table in ("machines", "node_volumes", "compute_machine_volumes"):
            connection.execute(f"CREATE TEMP TABLE {table} (updated_at timestamptz)")
        for table in ("ua_target_resources", "ua_actual_resources"):
            connection.execute(
                f"CREATE TEMP TABLE {table} (res_uuid uuid, kind text, value jsonb, hash text, full_hash text, updated_at timestamptz, master_hash text, master_full_hash text)"
            )
            for kind, value in [
                (
                    "node",
                    {
                        "disk_spec": {"kind": "root_disk", "size": 10},
                        "target_fields": {"disk_spec": {"kind": None, "size": None}},
                    },
                ),
                ("pool_volume", {"size": 10}),
                ("machine", {"cores": 2}),
                ("pool", {"storage_pools": [{"name": "default"}]}),
                ("unrelated", {"size": 10}),
            ]:
                connection.execute(
                    f"INSERT INTO {table} VALUES (%s, %s, %s, 'old', 'old-full', NULL, 'master', 'master-full')",
                    (uuid.uuid4(), kind, json.dumps(value)),
                )
        migration.migration_step.upgrade(Session())
        for table in ("ua_target_resources", "ua_actual_resources"):
            rows = {
                r["kind"]: r
                for r in connection.execute(f"SELECT * FROM {table}").fetchall()
            }
            assert (
                rows["node"]["value"]["disk_spec"]["speed"] == ic.DiskSpeed.WARM.value
            )
            assert (
                rows["node"]["value"]["target_fields"]["disk_spec"]["ephemeral"] is None
            )
            assert rows["pool_volume"]["value"]["ephemeral"] is False
            assert (
                rows["pool"]["value"]["storage_pools"][0]["speed"]
                == ic.DiskSpeed.WARM.value
            )
            assert rows["machine"]["hash"] == rows["node"]["hash"] == ""
            assert rows["unrelated"]["hash"] == "old"
        assert (
            connection.execute("SELECT disk_spec FROM nodes").fetchone()["disk_spec"][
                "ephemeral"
            ]
            is False
        )
        connection.rollback()


@pytest.mark.skipif(
    not os.environ.get("EXORDOS_STORAGE_TEST_DB"), reason="Requires test PostgreSQL"
)
@pytest.mark.parametrize(
    "previous_head", [None, "0002-add-repo-element-version-flags-24310b.py"]
)
def test_full_migration_graph_upgrades_legacy_schema(previous_head):
    import gcl_sdk.migrations as sdk_migrations
    from psycopg import sql
    from restalchemy.storage.sql import engines
    from restalchemy.storage.sql import migrations

    admin_dsn = os.environ["EXORDOS_STORAGE_TEST_DB"]
    name = "disk_migration_" + uuid.uuid4().hex
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(
                sql.Identifier(name)
            )
        )
    dsn = admin_dsn.rsplit("/", 1)[0] + "/" + name
    try:
        engines.engine_factory.configure_factory(db_url=dsn)
        sdk_engine = migrations.MigrationEngine(
            str(Path(sdk_migrations.__file__).parent)
        )
        sdk_engine.apply_migration(sdk_engine.get_latest_migration())
        core_engine = migrations.MigrationEngine(str(PATH.parent))
        if previous_head:
            core_engine.apply_migration(previous_head)
        core_engine.apply_migration(core_engine.get_latest_migration())
        with psycopg.connect(dsn) as connection:
            assert (
                connection.execute(
                    "SELECT column_default FROM information_schema.columns WHERE table_name='node_volumes' AND column_name='speed'"
                ).fetchone()[0]
                == "'WARM'::character varying"
            )
    finally:
        engines.engine_factory.destroy_engine()
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )
