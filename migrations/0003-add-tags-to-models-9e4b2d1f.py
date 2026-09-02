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

from restalchemy.storage.sql import migrations

from exordos_core.common import constants


class MigrationStep(migrations.AbstarctMigrationStep):
    def __init__(self):
        self._depends = ["0002-add-repo-element-version-flags-24310b.py"]

    @property
    def migration_id(self):
        return "9e4b2d1f-58cc-4372-a567-0e02b2c3d479"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        for table in constants.TABLES_TO_MODELS.keys():
            session.execute(
                f"ALTER TABLE public.{table} ADD COLUMN tags TEXT[] NOT NULL DEFAULT '{{}}';"
            )
            session.execute(
                f"CREATE INDEX idx_{table}_tags ON public.{table} USING GIN (tags);"
            )

    def downgrade(self, session):
        for table in constants.TABLES_TO_MODELS.keys():
            session.execute(f"DROP INDEX IF EXISTS public.idx_{table}_tags;")
            session.execute(f"ALTER TABLE public.{table} DROP COLUMN IF EXISTS tags;")


migration_step = MigrationStep()
