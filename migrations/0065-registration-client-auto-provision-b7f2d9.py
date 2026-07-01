# Copyright 2026 Genesis Corporation
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

import logging

from restalchemy.storage.sql import migrations

LOG = logging.getLogger(__name__)


class MigrationStep(migrations.AbstarctMigrationStep):
    def __init__(self):
        self._depends = ["0064-init-repo-tables-645142.py"]

    @property
    def migration_id(self):
        return "3c9d1f47-52ab-4e0e-9a86-7d02b64c1e58"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        sql_expressions = [
            """
            ALTER TABLE "iam_clients"
            ADD COLUMN IF NOT EXISTS "registration_auto_provision"
                BOOLEAN NOT NULL DEFAULT TRUE;
            """,
            """
            ALTER TABLE "iam_users"
            ADD COLUMN IF NOT EXISTS "registration_client" UUID DEFAULT NULL
                REFERENCES "iam_clients" ("uuid") ON DELETE SET NULL;
            """,
        ]

        for expr in sql_expressions:
            session.execute(expr, None)

    def downgrade(self, session):
        sql_expressions = [
            """
            ALTER TABLE "iam_users"
            DROP COLUMN IF EXISTS "registration_client";
            """,
            """
            ALTER TABLE "iam_clients"
            DROP COLUMN IF EXISTS "registration_auto_provision";
            """,
        ]

        for expr in sql_expressions:
            session.execute(expr, None)


migration_step = MigrationStep()
