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
        self._depends = ["0062-convert-varchar-to-jsonb-4d5e6f.py"]

    @property
    def migration_id(self):
        return "eb26ecbc-ab9e-427d-af94-b11247589ec0"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        sql_expressions = [
            """
            CREATE UNIQUE INDEX IF NOT EXISTS machine_pools_driver_spec_connection_uri_idx
            ON machine_pools ((driver_spec->>'connection_uri'));
            """,
        ]

        for expr in sql_expressions:
            session.execute(expr, None)

    def downgrade(self, session):
        sql_expressions = [
            """
            DROP INDEX IF EXISTS machine_pools_driver_spec_connection_uri_idx;
            """,
        ]

        for expr in sql_expressions:
            session.execute(expr, None)


migration_step = MigrationStep()
