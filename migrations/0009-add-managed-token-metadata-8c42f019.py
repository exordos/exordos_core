#    Copyright 2026 Genesis Corporation.
#
#    All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.

from restalchemy.storage.sql import migrations

UPGRADE = [
    """
    ALTER TABLE public.iam_tokens
        ADD COLUMN IF NOT EXISTS name character varying(255) DEFAULT '' NOT NULL,
        ADD COLUMN IF NOT EXISTS description character varying(255) DEFAULT '' NOT NULL
    """,
]

DOWNGRADE = [
    "ALTER TABLE public.iam_tokens DROP COLUMN IF EXISTS description",
    "ALTER TABLE public.iam_tokens DROP COLUMN IF EXISTS name",
]


class MigrationStep(migrations.AbstarctMigrationStep):
    def __init__(self):
        self._depends = ["0008-backfill-volume-storage-pool-4a14df.py"]

    @property
    def migration_id(self):
        return "8c42f019-e586-4d08-8be0-7829c8ec473a"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        for statement in UPGRADE:
            session.execute(statement)

    def downgrade(self, session):
        for statement in DOWNGRADE:
            session.execute(statement)


migration_step = MigrationStep()
