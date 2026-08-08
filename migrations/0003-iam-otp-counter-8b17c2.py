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

# Replay protection for TOTP codes.
#
# A TOTP code stays valid for its whole 30-second step, and until now
# nothing recorded that a code had already been spent -- so one intercepted
# code could be presented again, in parallel, for as long as the window
# lasted. This column remembers the last step a user consumed; the check
# itself is a conditional UPDATE against it, which is what makes two
# concurrent presentations of the same code resolve to one winner.
#
# Existing rows start at NULL, meaning "nothing spent yet": the next code
# such a user presents is accepted and sets the baseline.

from restalchemy.storage.sql import migrations


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0002-network-sdn-9e41c7.py"]

    @property
    def migration_id(self):
        return "8b17c2a4-6e93-4d5f-8c21-9f0a3b7e2d64"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        session.execute(
            """\
ALTER TABLE iam_users
    ADD COLUMN IF NOT EXISTS last_otp_counter BIGINT;
""",
            None,
        )

    def downgrade(self, session):
        session.execute(
            "ALTER TABLE iam_users DROP COLUMN IF EXISTS last_otp_counter;",
            None,
        )


migration_step = MigrationStep()
