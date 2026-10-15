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

DISK_TIERS = ("COLD", "WARM", "HOT")
DISK_TIER_CHECK = "('" + "', '".join(DISK_TIERS) + "')"


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0005-add-managed-tokens-5b1e8c47.py"]

    @property
    def migration_id(self):
        return "bfc9f6e4-7efc-47a5-aa8b-b93ece9656ca"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        session.execute(
            f"""
            ALTER TABLE node_volumes
                ADD COLUMN tier character varying(16)
                    DEFAULT 'WARM' NOT NULL,
                ADD COLUMN ephemeral boolean DEFAULT true NOT NULL,
                ADD CONSTRAINT node_volumes_tier_check
                    CHECK (tier IN {DISK_TIER_CHECK});
            """
        )
        session.execute(
            f"""
            ALTER TABLE compute_machine_volumes
                ADD COLUMN tier character varying(16)
                    DEFAULT 'WARM' NOT NULL,
                ADD COLUMN ephemeral boolean DEFAULT true NOT NULL,
                ADD COLUMN storage_pool character varying(255),
                ADD CONSTRAINT compute_machine_volumes_tier_check
                    CHECK (tier IN {DISK_TIER_CHECK});
            """
        )

    def downgrade(self, session):
        session.execute(
            """
            ALTER TABLE compute_machine_volumes
                DROP COLUMN tier,
                DROP COLUMN ephemeral,
                DROP COLUMN storage_pool;
            """
        )
        session.execute(
            """
            ALTER TABLE node_volumes
                DROP COLUMN tier,
                DROP COLUMN ephemeral;
            """
        )


migration_step = MigrationStep()
