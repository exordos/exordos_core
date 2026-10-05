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
        self._depends = ["0007-managed-storage-nodes-934dc2.py"]

    @property
    def migration_id(self):
        return "6ab127af-5341-4182-aa0e-3d0ccf1c526f"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        # Nodes and policy resources were created by migration 0005. Their
        # duplicate agent snapshots are no longer part of the public config.
        session.execute("""
            UPDATE storage_clusters SET
                driver_spec=jsonb_build_object('kind',driver_spec->>'kind',
                                               'endpoint',driver_spec->>'endpoint'),
                status=CASE WHEN agent IS NOT NULL THEN 'IN_PROGRESS' ELSE status END
            WHERE driver_spec->>'kind'='rawstor';
        """)

    def downgrade(self, session):
        session.execute("""
            UPDATE storage_clusters SET driver_spec=driver_spec ||
                '{"location":"","ost_endpoint":"","nodes":{},"pools":{},
                  "managed":true,"speed":"HOT","ephemeral":false}'::jsonb
            WHERE driver_spec->>'kind'='rawstor';
        """)


migration_step = MigrationStep()
