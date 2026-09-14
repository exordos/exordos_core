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
# lasted. This table remembers the last step each user consumed; the check
# itself is an INSERT ... ON CONFLICT against it, which is what makes two
# concurrent presentations of the same code resolve to one winner.
#
# It is a table of its own rather than a column on iam_users because it is
# written on every login, and doing that through the user row would keep
# moving iam_users.updated_at -- which is meant to say when the account was
# last changed, not when its owner last signed in.
#
# The key is the user's own uuid: one counter per user. Users start with no
# row at all, meaning "nothing spent yet", so the next code any of them
# presents is accepted and sets the baseline.

from restalchemy.storage.sql import migrations


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0000-squashed-current-7f2e4a.py"]

    @property
    def migration_id(self):
        return "8b17c2a4-6e93-4d5f-8c21-9f0a3b7e2d64"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        session.execute(
            """\
CREATE TABLE IF NOT EXISTS iam_user_otp_counters (
    uuid UUID NOT NULL,
    last_counter BIGINT NOT NULL,
    CONSTRAINT iam_user_otp_counters_pkey PRIMARY KEY (uuid),
    CONSTRAINT iam_user_otp_counters_uuid_fkey FOREIGN KEY (uuid)
        REFERENCES iam_users(uuid) ON UPDATE CASCADE ON DELETE CASCADE
);
""",
            None,
        )

    def downgrade(self, session):
        session.execute(
            "DROP TABLE IF EXISTS iam_user_otp_counters;",
            None,
        )


migration_step = MigrationStep()
