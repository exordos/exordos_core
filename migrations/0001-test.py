# Copyright 2016 Eugene Frolov <eugene@frolov.net.ru>
# Copyright 2025 Genesis Corporation
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

from restalchemy.storage.sql import migrations


class MigrationStep(migrations.AbstarctMigrationStep):
    def __init__(self):
        self._depends = [
            "0000-squashed-new-3b0440ab.py",
        ]

    @property
    def migration_id(self):
        return "01992e6e-53d5-449c-ab61-60f3bbc52651"

    @property
    def is_manual(self):
        return False


    def upgrade(self, session):
        session.execute("""
            ALTER TABLE "iam_users"
            ADD COLUMN bla VARCHAR(128) NOT NULL DEFAULT ''
        """)
        session.execute("""
            ALTER TABLE "iam_users"
            ADD COLUMN bla2 VARCHAR(15)
        """)

    def downgrade(self, session):
        session.execute("""
            ALTER TABLE "iam_users"
            DROP COLUMN bla
        """)
        session.execute("""
            ALTER TABLE "iam_users"
            DROP COLUMN bla2
        """)


migration_step = MigrationStep()