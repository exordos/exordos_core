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

"""Image builds apply HEAD; all feature branches must be reachable from it."""

from pathlib import Path

from restalchemy.storage.sql import migrations

ROOT = Path(__file__).resolve().parents[4]


def test_migration_head_reaches_every_branch():
    engine = migrations.MigrationEngine(str(ROOT / "migrations"))
    graph = engine.get_all_migrations()
    head = engine.get_latest_migration()
    reached = set()
    pending = [head]
    while pending:
        name = pending.pop()
        if name not in reached:
            reached.add(name)
            pending.extend(graph[name]["depends"])
    assert reached == set(graph)
