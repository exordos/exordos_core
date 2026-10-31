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


class MigrationStep(migrations.AbstarctMigrationStep):
    def __init__(self):
        self._depends = ["0010-node-power-state-8c74f270.py"]

    @property
    def migration_id(self):
        return "3e8a1472-c4ed-4dba-82ca-dac78fe129e9"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        session.execute("""
            ALTER TABLE public.iam_idp_authorization_info
                ADD COLUMN code_challenge character varying(43) DEFAULT NULL,
                ADD COLUMN code_challenge_method character varying(16) DEFAULT NULL
        """)

    def downgrade(self, session):
        session.execute("""
            ALTER TABLE public.iam_idp_authorization_info
                DROP COLUMN code_challenge_method,
                DROP COLUMN code_challenge
        """)


migration_step = MigrationStep()
