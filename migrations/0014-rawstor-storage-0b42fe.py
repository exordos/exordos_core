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

POOL_STATUSES = ("ACTIVE", "DISABLED", "MAINTENANCE", "IN_PROGRESS", "DELETING")
POOL_STATUS_CHECK = "('" + "', '".join(POOL_STATUSES) + "')"


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0013-realm-scoped-dns-5bd99b.py"]

    @property
    def migration_id(self):
        return "0b42fe70-dc81-4d01-9804-b4899cf18781"

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
                capacity_info jsonb NOT NULL DEFAULT '{{}}'::jsonb,
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

        session.execute("""
            CREATE TABLE storage_nodes (
                uuid uuid PRIMARY KEY, name varchar(255) NOT NULL,
                description varchar(255) NOT NULL DEFAULT '',
                cluster uuid NOT NULL REFERENCES storage_clusters(uuid),
                driver_spec jsonb NOT NULL,
                agent uuid,
                builder uuid,
                status varchar(32) NOT NULL DEFAULT 'NEW',
                created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(cluster, name)
            );
            CREATE TABLE storage_pools (
                uuid uuid PRIMARY KEY, name varchar(255) NOT NULL,
                description varchar(255) NOT NULL DEFAULT '',
                cluster uuid NOT NULL REFERENCES storage_clusters(uuid),
                tier varchar(16) NOT NULL CHECK (tier IN ('COLD','WARM','HOT')),
                ephemeral boolean NOT NULL,
                driver_spec jsonb NOT NULL,
                created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(cluster, name)
            );
            CREATE UNIQUE INDEX storage_nodes_endpoint_unique ON storage_nodes ((driver_spec->>'endpoint'));
            CREATE UNIQUE INDEX storage_clusters_name_unique ON storage_clusters(name);
        """)

        # Rebuild existing volume resources with the Rawstor-capable payload.
        session.execute(
            "UPDATE compute_machine_volumes SET updated_at=CURRENT_TIMESTAMP"
        )

    def downgrade(self, session):
        session.execute(
            "DROP TABLE storage_pools; DROP TABLE storage_nodes; DROP TABLE storage_clusters;"
        )


migration_step = MigrationStep()
