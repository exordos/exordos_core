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
        self._depends = ["0007-repo-manual-refresh-a2b9da.py"]

    @property
    def migration_id(self):
        return "4a14dfef-bad6-4d4b-9efd-c31f1ee4c193"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        # Volumes created before disk scheduling have no storage pool pin.
        # At that time every machine pool had a single storage pool, which
        # is the first entry of the driver spec now, so every legacy volume
        # lives there. Touch the volumes so the builder regenerates their
        # target resources with the pin.
        session.execute(
            """
            UPDATE compute_machine_volumes v
            SET storage_pool = NULLIF(
                    CASE jsonb_typeof(p.driver_spec->'storage_pool')
                        WHEN 'array' THEN p.driver_spec->'storage_pool'->0->>'name'
                        WHEN 'string' THEN p.driver_spec->>'storage_pool'
                    END,
                    ''
                ),
                updated_at = clock_timestamp()
            FROM machine_pools p
            WHERE v.pool = p.uuid
                AND v.storage_pool IS NULL;
            """
        )

    def downgrade(self, session):
        # The pin is a valid placement, there is nothing to restore.
        pass


migration_step = MigrationStep()
