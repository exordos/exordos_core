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


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0006-disk-scheduling-resources-96f43b.py"]

    @property
    def migration_id(self):
        return "a2b9da03-7d37-44c6-84ca-6e3215eb8467"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        session.execute(
            "ALTER TABLE repo_repositories ADD COLUMN "
            "refresh_requested boolean NOT NULL DEFAULT false",
            None,
        )

    def downgrade(self, session):
        session.execute(
            "ALTER TABLE repo_repositories DROP COLUMN refresh_requested", None
        )


migration_step = MigrationStep()
