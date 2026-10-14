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
        self._depends = ["0007-storage-clusters-3d8a1c.py"]

    @property
    def migration_id(self):
        return "8ce6d2d5-6dbb-47a3-a2f3-d8c292d1bc1a"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        session.execute(
            "ALTER TABLE storage_clusters ADD COLUMN capacity_info jsonb NOT NULL DEFAULT '{}'::jsonb"
        )
        session.execute("""
            CREATE TABLE storage_nodes (
                uuid uuid PRIMARY KEY, name varchar(255) NOT NULL,
                description varchar(255) NOT NULL DEFAULT '',
                cluster uuid NOT NULL REFERENCES storage_clusters(uuid),
                driver_spec jsonb NOT NULL,
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
        # Preserve existing standalone OSTs and their original single-pool policy.
        session.execute("""
            INSERT INTO storage_nodes(uuid,name,cluster,driver_spec)
            SELECT uuid,name,uuid,jsonb_build_object('kind','rawstor',
                'endpoint',driver_spec->>'ost_endpoint','failure_domain_path',uuid::text,
                'weight',100,'location','','bind_address','')
            FROM storage_clusters WHERE COALESCE(driver_spec->>'ost_endpoint','') <> '';
        """)
        session.execute("""
            INSERT INTO storage_pools(uuid,name,cluster,tier,ephemeral,driver_spec)
            SELECT (p->>'uuid')::uuid,p->>'name',c.uuid,p->>'tier',
                   (p->>'ephemeral')::boolean,jsonb_build_object('kind','rawstor','mirrors',1,'chunk_size',1073741824,'failure_domain','server')
            FROM storage_clusters c, unnest(c.storage_pools) p
            WHERE p->>'uuid' IS NOT NULL;
        """)

        # Rebuild agent assignments from SQL after adding the rawstor payload.
        # This also refreshes existing qcow2 resources without rewriting JSON.
        session.execute(
            "UPDATE compute_machine_volumes SET updated_at=CURRENT_TIMESTAMP"
        )

    def downgrade(self, session):
        session.execute(
            "DROP TABLE storage_pools; DROP TABLE storage_nodes; DROP INDEX storage_clusters_name_unique"
        )
        session.execute("ALTER TABLE storage_clusters DROP COLUMN capacity_info")


migration_step = MigrationStep()
