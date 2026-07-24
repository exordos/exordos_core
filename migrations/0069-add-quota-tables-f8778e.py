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

import logging

from restalchemy.storage.sql import migrations

LOG = logging.getLogger(__name__)


class MigrationStep(migrations.AbstarctMigrationStep):
    def __init__(self):
        self._depends = ["0068-fix-resource-status-hash-check-437c89.py"]

    @property
    def migration_id(self):
        return "f8778ebb-ce15-4b72-9860-305b84f3f7ae"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        sql_expressions = [
            # quota_limits
            """
            CREATE TABLE IF NOT EXISTS quota_limits (
                uuid UUID NOT NULL PRIMARY KEY,
                project_id UUID NOT NULL,
                resource_name VARCHAR(255) NOT NULL,
                field_name VARCHAR(255) NOT NULL DEFAULT '',
                "limit" INTEGER NOT NULL,
                "created_at" TIMESTAMP(6) NOT NULL DEFAULT NOW(),
                "updated_at" TIMESTAMP(6) NOT NULL DEFAULT NOW()
            );
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS quota_limits_project_resource_field_name_idx
                ON quota_limits (project_id, resource_name, field_name);
            """,
        ]

        for expr in sql_expressions:
            session.execute(expr, None)

    def downgrade(self, session):
        tables = [
            "quota_limits",
        ]

        for table_name in tables:
            self._delete_table_if_exists(session, table_name)


migration_step = MigrationStep()
