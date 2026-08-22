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
import pathlib
import uuid as sys_uuid

import pytest

MIGRATION_PATH = (
    pathlib.Path(__file__).parents[4]
    / "migrations"
    / "0002-add-repo-element-version-flags-24310b.py"
)


class FakeSession:
    """Session that answers the backfill query and records the updates.

    Rows are mappings because the PostgreSQL session builds its cursor
    with `row_factory=dict_row`.
    """

    def __init__(self, rows):
        self._rows = [
            dict(zip(("uuid", "repository", "name", "version"), row)) for row in rows
        ]
        self.updates = []

    def execute(self, statement, values=None):
        return self

    def fetchall(self):
        return self._rows

    def execute_many(self, statement, values):
        self.updates.extend(values)


@pytest.fixture
def migration_step():
    spec = importlib.util.spec_from_file_location(
        "repo_element_version_flags_migration", MIGRATION_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.migration_step


def test_backfill_marks_stable_and_latest(migration_step):
    repo = sys_uuid.uuid4()
    other_repo = sys_uuid.uuid4()
    old = sys_uuid.uuid4()
    latest = sys_uuid.uuid4()
    prerelease = sys_uuid.uuid4()
    invalid = sys_uuid.uuid4()
    other_repo_element = sys_uuid.uuid4()
    other_name = sys_uuid.uuid4()
    session = FakeSession(
        [
            (old, repo, "element", "1.0.0"),
            (latest, repo, "element", "2.0.0"),
            (prerelease, repo, "element", "3.0.0-beta.1"),
            (invalid, repo, "element", "not-a-version"),
            (other_repo_element, other_repo, "element", "1.0.0"),
            (other_name, repo, "other-element", "1.0.0"),
        ]
    )

    migration_step._backfill_version_flags(session)

    assert dict(
        (element_uuid, (stable, is_latest))
        for stable, is_latest, element_uuid in session.updates
    ) == {
        old: (True, False),
        latest: (True, True),
        other_repo_element: (True, True),
        other_name: (True, True),
    }


def test_backfill_without_elements_does_not_update(migration_step):
    session = FakeSession([])

    migration_step._backfill_version_flags(session)

    assert session.updates == []
