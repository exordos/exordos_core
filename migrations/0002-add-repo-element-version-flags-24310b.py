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
            ADD COLUMN IF NOT EXISTS published timestamp(6) without time zone DEFAULT now(),
            ADD COLUMN IF NOT EXISTS stable boolean NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS latest boolean NOT NULL DEFAULT false;
            """
        )
        session.execute(
            "CREATE INDEX IF NOT EXISTS idx_repo_elements_project_id ON repo_elements (project_id)"
        )
        session.execute(
            "CREATE INDEX IF NOT EXISTS idx_repo_elements_tags ON repo_elements USING GIN (tags)"
        )

    def downgrade(self, session):
        session.execute("DROP INDEX IF EXISTS idx_repo_elements_project_id")
        session.execute("DROP INDEX IF EXISTS idx_repo_elements_tags")
        session.execute(
            """
            ALTER TABLE repo_elements
            DROP COLUMN IF EXISTS tags,
            DROP COLUMN IF EXISTS stable,
            DROP COLUMN IF EXISTS published,
            DROP COLUMN IF EXISTS latest;
            """
        )


migration_step = MigrationStep()
