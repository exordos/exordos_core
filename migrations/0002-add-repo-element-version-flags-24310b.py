# Copyright 2016 Eugene Frolov <eugene@frolov.net.ru>
#
# All Rights Reserved.
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

from packaging import version as packaging_version
from restalchemy.storage.sql import migrations


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0001-zero-entities-5a7a0e.py"]

    @property
    def migration_id(self):
        return "24310bc7-03bf-4ecc-af05-7199ae6d5460"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        session.execute(
            """
            ALTER TABLE repo_elements
            ADD COLUMN IF NOT EXISTS tags TEXT[] NOT NULL DEFAULT '{}',
            ADD COLUMN IF NOT EXISTS published_at timestamp(6) without time zone DEFAULT now(),
            ADD COLUMN IF NOT EXISTS stable boolean NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS latest boolean NOT NULL DEFAULT false;
            """
        )
        # The column default stamps every existing element with the
        # migration time, which would make old elements look newly
        # published. The runtime falls back to created_at when the
        # inventory carries no publication metadata, so use it here too.
        session.execute("UPDATE repo_elements SET published_at = created_at")
        session.execute(
            "CREATE INDEX IF NOT EXISTS idx_repo_elements_project_id ON repo_elements (project_id)"
        )
        session.execute(
            "CREATE INDEX IF NOT EXISTS idx_repo_elements_tags ON repo_elements USING GIN (tags)"
        )
        # The store selects its catalogue by these two flags, so index the
        # rows it looks at instead of scanning the whole table.
        session.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_repo_elements_latest_stable
            ON repo_elements (name) WHERE stable AND latest
            """
        )
        self._backfill_version_flags(session)

    def _backfill_version_flags(self, session):
        """Set `stable` and `latest` for elements that predate the columns.

        A refresh only calculates the flags for newly discovered versions,
        so without this the elements already in the database would stay out
        of the store until their repository publishes a new version.
        """
        rows = session.execute(
            "SELECT uuid, repository, name, version FROM repo_elements"
        ).fetchall()

        stable_uuids = []
        latest_by_element = {}
        for row in rows:
            try:
                parsed_version = packaging_version.parse(row["version"])
            except packaging_version.InvalidVersion:
                continue
            if parsed_version.is_prerelease:
                continue

            stable_uuids.append(row["uuid"])
            key = (row["repository"], row["name"])
            current = latest_by_element.get(key)
            if current is None or parsed_version > current[0]:
                latest_by_element[key] = (parsed_version, row["uuid"])

        if not stable_uuids:
            return

        latest_uuids = {element_uuid for _, element_uuid in latest_by_element.values()}
        session.execute_many(
            "UPDATE repo_elements SET stable = %s, latest = %s WHERE uuid = %s",
            [
                (True, element_uuid in latest_uuids, element_uuid)
                for element_uuid in stable_uuids
            ],
        )

    def downgrade(self, session):
        session.execute("DROP INDEX IF EXISTS idx_repo_elements_project_id")
        session.execute("DROP INDEX IF EXISTS idx_repo_elements_tags")
        session.execute("DROP INDEX IF EXISTS idx_repo_elements_latest_stable")
        session.execute(
            """
            ALTER TABLE repo_elements
            DROP COLUMN IF EXISTS tags,
            DROP COLUMN IF EXISTS stable,
            DROP COLUMN IF EXISTS published_at,
            DROP COLUMN IF EXISTS latest;
            """
        )


migration_step = MigrationStep()
