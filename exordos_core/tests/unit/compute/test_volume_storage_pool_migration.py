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

import psycopg
from psycopg.rows import dict_row
import pytest

PATH = (
    Path(__file__).parents[4] / "migrations/0008-backfill-volume-storage-pool-4a14df.py"
)
spec = importlib.util.spec_from_file_location("volume_storage_pool_migration", PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


@pytest.mark.skipif(
    not os.environ.get("EXORDOS_STORAGE_TEST_DB"), reason="Requires test PostgreSQL"
)
def test_migration_pins_legacy_volumes_to_driver_storage_pool():
    class Session:
        def execute(self, statement, values=None):
            return connection.execute(statement, values)

    pools = {
        "local": {
            "kind": "exordos_local_hyper",
            "storage_pool": [
                {"name": "legacy", "tier": "WARM", "ephemeral": True},
                {"name": "added", "tier": "HOT", "ephemeral": True},
            ],
        },
        "libvirt": {"kind": "libvirt", "storage_pool": "legacy-libvirt"},
        "empty": {"kind": "exordos_local_hyper", "storage_pool": []},
    }
    pool_uuids = {name: uuid.uuid4() for name in pools}
    # volume name -> (pool name, current pin, expected pin)
    volumes = {
        "legacy": ("local", None, "legacy"),
        "pinned": ("local", "added", "added"),
        "legacy-libvirt": ("libvirt", None, "legacy-libvirt"),
        "empty": ("empty", None, None),
        "unscheduled": (None, None, None),
    }

    with psycopg.connect(
        os.environ["EXORDOS_STORAGE_TEST_DB"],
        row_factory=dict_row,
        options="-c client_encoding=UTF8",
    ) as connection:
        connection.execute(
            "CREATE TEMP TABLE machine_pools (uuid uuid, driver_spec jsonb)"
        )
        connection.execute(
            "CREATE TEMP TABLE compute_machine_volumes "
            "(uuid uuid, name text, pool uuid, storage_pool text, "
            "updated_at timestamptz)"
        )
        for name, driver_spec in pools.items():
            connection.execute(
                "INSERT INTO machine_pools VALUES (%s, %s)",
                (pool_uuids[name], json.dumps(driver_spec)),
            )
        for name, (pool, pin, _) in volumes.items():
            connection.execute(
                "INSERT INTO compute_machine_volumes "
                "VALUES (%s, %s, %s, %s, '2000-01-01')",
                (uuid.uuid4(), name, pool_uuids.get(pool), pin),
            )

        migration.migration_step.upgrade(Session())

        rows = {
            r["name"]: r
            for r in connection.execute(
                "SELECT name, storage_pool, updated_at > '2000-01-01' AS touched "
                "FROM compute_machine_volumes"
            ).fetchall()
        }
        for name, (_, _, expected) in volumes.items():
            assert rows[name]["storage_pool"] == expected, name
        assert rows["legacy"]["touched"]
        assert rows["legacy-libvirt"]["touched"]
        assert not rows["pinned"]["touched"]
        assert not rows["unscheduled"]["touched"]

        migration.migration_step.downgrade(Session())
        assert (
            connection.execute(
                "SELECT storage_pool FROM compute_machine_volumes WHERE name = 'legacy'"
            ).fetchone()["storage_pool"]
            == "legacy"
        )
        connection.rollback()
