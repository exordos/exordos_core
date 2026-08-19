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

import datetime
import importlib.util
import pathlib
import uuid as sys_uuid

import pytest

from exordos_core.common import constants as c

MIGRATION_PATH = (
    pathlib.Path(__file__).parents[5]
    / "migrations"
    / "0002-add-repo-element-version-flags-24310b.py"
)

# What the column defaults leave behind for elements that predate the
# migration: the time the migration ran, and both flags cleared.
MIGRATION_TIME = datetime.datetime(2026, 8, 21, 12, 0, 0)

# Elements that predate the migration: version, created_at, stable, latest
ELEMENTS = (
    ("1.0.0", datetime.datetime(2020, 1, 1, 10, 20, 30), True, False),
    ("2.0.0", datetime.datetime(2020, 2, 1, 10, 20, 30), True, True),
    ("3.0.0b1", datetime.datetime(2020, 3, 1, 10, 20, 30), False, False),
)


@pytest.fixture
def migration_step():
    spec = importlib.util.spec_from_file_location(
        "repo_element_version_flags_migration", MIGRATION_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.migration_step


class TestRepoElementVersionFlagsMigration:
    """Coverage for the backfill the migration performs on existing rows."""

    @pytest.fixture
    def backfilled_elements(self, user_api, migration_step):
        """Seed pre-migration rows, then run the migration step over them."""
        repository_uuid = sys_uuid.uuid4()
        element_uuids = {}

        with user_api.engine.session_manager() as session:
            session.execute(
                "INSERT INTO repo_repositories (uuid, name, project_id)"
                " VALUES (%s, %s, %s)",
                (repository_uuid, f"migration-repo-{repository_uuid}", c.EM_PROJECT_ID),
            )
            for version, created_at, _, _ in ELEMENTS:
                element_uuid = sys_uuid.uuid4()
                element_uuids[version] = element_uuid
                session.execute(
                    "INSERT INTO repo_elements (uuid, name, project_id, repository,"
                    " version, created_at, published_at, stable, latest)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, false, false)",
                    (
                        element_uuid,
                        "migration-element",
                        c.EM_PROJECT_ID,
                        repository_uuid,
                        version,
                        created_at,
                        MIGRATION_TIME,
                    ),
                )

        # The DDL is written with IF NOT EXISTS, so replaying the step on an
        # already migrated schema exercises the backfill alone.
        with user_api.engine.session_manager() as session:
            migration_step.upgrade(session)

        return element_uuids

    def _fetch(self, user_api, element_uuid):
        with user_api.engine.session_manager() as session:
            return session.execute(
                "SELECT created_at, published_at, stable, latest"
                " FROM repo_elements WHERE uuid = %s",
                (element_uuid,),
            ).fetchone()

    @pytest.mark.parametrize("version, created_at, stable, latest", ELEMENTS)
    def test_published_at_is_backfilled_from_created_at(
        self, user_api, backfilled_elements, version, created_at, stable, latest
    ):
        row = self._fetch(user_api, backfilled_elements[version])

        assert row["created_at"] == created_at
        assert row["published_at"] == created_at

    @pytest.mark.parametrize("version, created_at, stable, latest", ELEMENTS)
    def test_version_flags_are_backfilled(
        self, user_api, backfilled_elements, version, created_at, stable, latest
    ):
        row = self._fetch(user_api, backfilled_elements[version])

        assert row["stable"] is stable
        assert row["latest"] is latest
