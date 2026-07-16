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
        self._depends = ["0065-registration-client-auto-provision-b7f2d9.py"]

    @property
    def migration_id(self):
        return "746143b5-3bf1-41d0-af58-5e9461767811"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        session.execute(
            "ALTER TABLE secret_certificates "
            "ADD COLUMN IF NOT EXISTS ca_cert TEXT NULL",
            None,
        )
        session.execute(
            "ALTER TABLE storage_certs ADD COLUMN IF NOT EXISTS ca_key TEXT NULL",
            None,
        )
        session.execute(
            "ALTER TABLE storage_certs ADD COLUMN IF NOT EXISTS ca_cert TEXT NULL",
            None,
        )

    def downgrade(self, session):
        session.execute(
            "ALTER TABLE storage_certs DROP COLUMN IF EXISTS ca_cert",
            None,
        )
        session.execute(
            "ALTER TABLE storage_certs DROP COLUMN IF EXISTS ca_key",
            None,
        )
        session.execute(
            "ALTER TABLE secret_certificates DROP COLUMN IF EXISTS ca_cert",
            None,
        )


migration_step = MigrationStep()
