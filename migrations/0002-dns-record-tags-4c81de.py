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

# Tags on DNS records, so a row carries who created it.
#
# A managed realm mirrors its own zone into this installation and, until
# now, reconciled it by deleting every record it did not have locally --
# including records this installation's own services put there. The owner
# tag is what lets the mirror tell those apart; the GIN index is what
# keeps asking for "my records" cheap as a zone grows.
#
# Rows that predate this migration carry no tag, which reads as "owner
# unknown" and means no reconciler will remove them. That is the intended
# outcome: nothing is retro-assigned to an owner nobody can verify.

from restalchemy.storage.sql import migrations


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0001-zero-entities-5a7a0e.py"]

    @property
    def migration_id(self):
        return "4c81de92-3f5b-4a6e-9d21-7b0c8e5f1a34"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        for expression in (
            """\
ALTER TABLE dns_records
    ADD COLUMN IF NOT EXISTS tags TEXT[] NOT NULL DEFAULT '{}';
""",
            """\
CREATE INDEX IF NOT EXISTS dns_records_tags_idx
    ON dns_records USING GIN (tags);
""",
        ):
            session.execute(expression, None)

    def downgrade(self, session):
        for expression in (
            "DROP INDEX IF EXISTS dns_records_tags_idx;",
            "ALTER TABLE dns_records DROP COLUMN IF EXISTS tags;",
        ):
            session.execute(expression, None)


migration_step = MigrationStep()
