# Copyright 2016 Eugene Frolov <eugene@frolov.net.ru>
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
        self._depends = ["0061-rename-rule-verifier-to-action-a3b4c5.py"]

    @property
    def migration_id(self):
        return "4d5e6f78-9abc-def0-1234-56789abcdef0"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        # Remove defaults before changing column type
        drop_default_expressions = [
            "ALTER TABLE compute_networks ALTER COLUMN driver_spec DROP DEFAULT;",
        ]

        for expr in drop_default_expressions:
            session.execute(expr, None)

        # Convert varchar columns to jsonb
        sql_expressions = [
            """
            ALTER TABLE compute_networks
                ALTER COLUMN driver_spec TYPE jsonb
                USING driver_spec::jsonb;
            """,
            """
            ALTER TABLE machine_pools
                ALTER COLUMN driver_spec TYPE jsonb
                USING driver_spec::jsonb;
            """,
        ]

        for expr in sql_expressions:
            session.execute(expr, None)

        # Set new defaults for jsonb columns
        default_expressions = [
            "ALTER TABLE compute_networks ALTER COLUMN driver_spec SET DEFAULT '{}'::jsonb;",
            "ALTER TABLE machine_pools ALTER COLUMN driver_spec SET DEFAULT '{}'::jsonb;",
        ]

        for expr in default_expressions:
            session.execute(expr, None)

    def downgrade(self, session):
        # Remove defaults before changing column type
        drop_default_expressions = [
            "ALTER TABLE compute_networks ALTER COLUMN driver_spec DROP DEFAULT;",
        ]

        for expr in drop_default_expressions:
            session.execute(expr, None)

        # Convert jsonb columns to varchar
        sql_expressions = [
            """
            ALTER TABLE compute_networks
                ALTER COLUMN driver_spec TYPE varchar(512)
                USING driver_spec::varchar(512);
            """,
            """
            ALTER TABLE machine_pools
                ALTER COLUMN driver_spec TYPE varchar(512)
                USING driver_spec::varchar(512);
            """,
        ]

        for expr in sql_expressions:
            session.execute(expr, None)

        # Set defaults for varchar columns
        default_expressions = [
            "ALTER TABLE compute_networks ALTER COLUMN driver_spec SET DEFAULT '{}';",
        ]

        for expr in default_expressions:
            session.execute(expr, None)


migration_step = MigrationStep()
