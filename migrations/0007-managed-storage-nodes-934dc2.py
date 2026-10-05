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


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0006-merge-rawstor-secrets-7a8e91.py"]

    @property
    def migration_id(self):
        return "934dc209-6433-4ab2-95ec-878a9f7d1332"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        # Existing endpoints remain externally managed until explicitly adopted.
        session.execute("""
            ALTER TABLE storage_nodes
                ADD COLUMN agent uuid,
                ADD COLUMN builder uuid,
                ADD COLUMN location varchar(2048) NOT NULL DEFAULT '',
                ADD COLUMN bind_address varchar(255) NOT NULL DEFAULT '',
                ADD COLUMN status varchar(32) NOT NULL DEFAULT 'ACTIVE';
            ALTER TABLE storage_nodes ALTER COLUMN status SET DEFAULT 'NEW';
        """)

    def downgrade(self, session):
        session.execute("""
            ALTER TABLE storage_nodes DROP COLUMN agent, DROP COLUMN builder,
                DROP COLUMN location, DROP COLUMN bind_address, DROP COLUMN status;
        """)


migration_step = MigrationStep()
