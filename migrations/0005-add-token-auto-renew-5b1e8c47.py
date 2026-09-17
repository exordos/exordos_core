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
        ADD COLUMN IF NOT EXISTS auto_renew boolean DEFAULT false NOT NULL
    """,
    # Login sessions fill the table, and the renewal loop only looks
    # for the few tokens that renew themselves.
    """
    CREATE INDEX IF NOT EXISTS iam_tokens_auto_renew_idx
        ON public.iam_tokens USING btree (uuid) WHERE auto_renew
    """,
]

DOWNGRADE = [
    "DROP INDEX IF EXISTS public.iam_tokens_auto_renew_idx",
    "ALTER TABLE public.iam_tokens DROP COLUMN IF EXISTS auto_renew",
]


class MigrationStep(migrations.AbstarctMigrationStep):
    def __init__(self):
        self._depends = ["0004-add-opaque-secrets-362f6d21.py"]

    @property
    def migration_id(self):
        return "5b1e8c47-2d0a-4f6e-9c3b-7a41d2e9f018"

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
