#    Copyright 2026 Genesis Corporation.
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


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0013-realm-scoped-dns-5bd99b.py"]

    @property
    def migration_id(self):
        return "4e83cda1-8486-4926-8b4e-b93e42a84446"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        for table in ("em_manifests", "em_elements"):
            session.execute(
                f"ALTER TABLE public.{table} ALTER COLUMN description "
                "TYPE text"
            )

    def downgrade(self, session):
        for table in ("em_manifests", "em_elements"):
            session.execute(
                f"ALTER TABLE public.{table} ALTER COLUMN description "
                "TYPE character varying(255)"
            )


migration_step = MigrationStep()
