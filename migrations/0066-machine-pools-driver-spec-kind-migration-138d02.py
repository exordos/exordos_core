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


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = [
            "0065-registration-client-auto-provision-b7f2d9.py",
        ]

    @property
    def migration_id(self):
        return "138d02f4-c3db-45cc-ba81-f3153c211731"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        # Rename "driver" key to "kind" in machine_pools.driver_spec JSONB
        # column. Migrate empty '{}' specs to dummy spec since driver_spec
        # is now a required field.
        sql_expressions = [
            """
            UPDATE machine_pools
            SET driver_spec = jsonb_set(
                driver_spec - 'driver',
                '{kind}',
                driver_spec->'driver'
            ),
                updated_at = current_timestamp
            WHERE driver_spec ? 'driver';
            """,
            """
            ALTER TABLE machine_pools ALTER COLUMN driver_spec DROP DEFAULT;
            """,
            """
            UPDATE machine_pools
            SET driver_spec = '{"kind": "dummy"}'::jsonb,
                updated_at = current_timestamp
            WHERE driver_spec = '{}'::jsonb;
            """,
            # Drop the unique index on connection_uri since
            # exordos_local_hyper pools may share the same connection_uri.
            """
            DROP INDEX IF EXISTS machine_pools_driver_spec_connection_uri_idx;
            """,
        ]

        for expr in sql_expressions:
            session.execute(expr, None)

    def downgrade(self, session):
        sql_expressions = [
            """
            UPDATE machine_pools
            SET driver_spec = jsonb_set(
                driver_spec - 'kind',
                '{driver}',
                driver_spec->'kind'
            ),
                updated_at = current_timestamp
            WHERE driver_spec ? 'kind';
            """,
            """
            ALTER TABLE machine_pools
            ALTER COLUMN driver_spec SET DEFAULT '{}'::jsonb;
            """,
            """
            UPDATE machine_pools
            SET driver_spec = '{}'::jsonb,
                updated_at = current_timestamp
            WHERE driver_spec = '{"kind": "dummy"}'::jsonb;
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS machine_pools_driver_spec_connection_uri_idx
            ON machine_pools ((driver_spec->>'connection_uri'));
            """,
        ]

        for expr in sql_expressions:
            session.execute(expr, None)


migration_step = MigrationStep()
