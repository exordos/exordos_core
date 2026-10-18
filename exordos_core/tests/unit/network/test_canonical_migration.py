# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0

from pathlib import Path

from restalchemy.storage.sql import migrations


MIGRATIONS = Path(__file__).parents[4] / "migrations"


def test_sdn_migrations_follow_master():
    engine = migrations.MigrationEngine(migrations_path=str(MIGRATIONS))
    chain = [
        "0006-disk-scheduling-resources-96f43b.py",
        "0007-repo-manual-refresh-a2b9da.py",
        "0008-backfill-volume-storage-pool-4a14df.py",
        "0009-add-managed-token-metadata-8c42f019.py",
        "0010-network-sdn-9e41c7.py",
    ]
    assert engine.get_latest_migration() == chain[-1]
    steps = engine.get_all_migrations()
    for parent, child in zip(chain, chain[1:]):
        assert steps[child]["depends"] == [parent]
