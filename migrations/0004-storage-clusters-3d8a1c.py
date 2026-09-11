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

POOL_STATUSES = ("ACTIVE", "DISABLED", "MAINTENANCE", "IN_PROGRESS")
POOL_STATUS_CHECK = "('" + "', '".join(POOL_STATUSES) + "')"


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0003-volumes-speed-ephemeral-fba1de.py"]

    @property
    def migration_id(self):
        return "3d8a1c4e-6b2f-4a1d-9c3e-1f7a2b5d0e6c"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        session.execute(
            f"""
            CREATE TABLE public.storage_clusters (
                uuid uuid NOT NULL,
                name character varying(255) NOT NULL,
                description character varying(255) NOT NULL,
                driver_spec jsonb NOT NULL,
                status character varying(32) NOT NULL,
                storage_pools jsonb[] DEFAULT '{{}}'::jsonb[],
                builder uuid,
                agent uuid,
                created_at timestamp without time zone
                    DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamp without time zone
                    DEFAULT CURRENT_TIMESTAMP NOT NULL,
                CONSTRAINT storage_clusters_pkey PRIMARY KEY (uuid),
                CONSTRAINT storage_clusters_status_check
                    CHECK (status IN {POOL_STATUS_CHECK})
            );
            """
        )
        session.execute(
            """
            ALTER TABLE compute_machine_volumes
                ADD COLUMN storage_location character varying(2048);
            """
        )

    def downgrade(self, session):
        session.execute(
            """
            ALTER TABLE compute_machine_volumes
                DROP COLUMN storage_location;
            """
        )
        session.execute("DROP TABLE public.storage_clusters;")


migration_step = MigrationStep()
