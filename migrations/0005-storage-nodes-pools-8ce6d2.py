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
        self._depends = ["0004-storage-clusters-3d8a1c.py"]

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
        session.execute(
            "ALTER TABLE compute_machine_volumes ADD COLUMN storage_policy jsonb NOT NULL DEFAULT '{}'::jsonb"
        )
        session.execute("""
            CREATE TABLE storage_nodes (
                uuid uuid PRIMARY KEY, name varchar(255) NOT NULL,
                description varchar(255) NOT NULL DEFAULT '',
                cluster uuid NOT NULL REFERENCES storage_clusters(uuid),
                kind varchar(32) NOT NULL CHECK (kind = 'rawstor'),
                endpoint varchar(2048) NOT NULL UNIQUE,
                failure_domain_path varchar(255) NOT NULL,
                weight bigint NOT NULL CHECK (weight > 0),
                created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(cluster, name)
            );
            CREATE TABLE storage_pools (
                uuid uuid PRIMARY KEY, name varchar(255) NOT NULL,
                description varchar(255) NOT NULL DEFAULT '',
                cluster uuid NOT NULL REFERENCES storage_clusters(uuid),
                speed varchar(16) NOT NULL CHECK (speed IN ('COLD','WARM','HOT')),
                ephemeral boolean NOT NULL,
                mirrors integer NOT NULL CHECK (mirrors BETWEEN 1 AND 255),
                chunk_size bigint NOT NULL CHECK (chunk_size > 0),
                failure_domain varchar(16) NOT NULL,
                created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(cluster, name)
            );
            CREATE UNIQUE INDEX storage_clusters_name_unique ON storage_clusters(name);
        """)
        # Preserve existing standalone OSTs and their original single-pool policy.
        session.execute("""
            INSERT INTO storage_nodes(uuid,name,cluster,kind,endpoint,failure_domain_path,weight)
            SELECT uuid,name,uuid,'rawstor',driver_spec->>'ost_endpoint',uuid::text,1
            FROM storage_clusters WHERE COALESCE(driver_spec->>'ost_endpoint','') <> '';
        """)
        session.execute("""
            INSERT INTO storage_pools(uuid,name,cluster,speed,ephemeral,mirrors,chunk_size,failure_domain)
            SELECT (p->>'uuid')::uuid,p->>'name',c.uuid,p->>'speed',
                   (p->>'ephemeral')::boolean,1,1073741824,'server'
            FROM storage_clusters c, unnest(c.storage_pools) p
            WHERE p->>'uuid' IS NOT NULL;
        """)

        session.execute("""
            UPDATE compute_machine_volumes v SET storage_policy =
                jsonb_build_object('pool_uuid',p.uuid::text,'mirrors',p.mirrors,
                                   'chunk_size',p.chunk_size,'failure_domain',p.failure_domain)
            FROM storage_pools p JOIN storage_clusters c ON c.uuid=p.cluster
            WHERE v.storage_location=c.driver_spec->>'endpoint' AND v.storage_pool=p.name;
        """)

    def downgrade(self, session):
        session.execute(
            "DROP TABLE storage_pools; DROP TABLE storage_nodes; DROP INDEX storage_clusters_name_unique"
        )
        session.execute(
            "ALTER TABLE compute_machine_volumes DROP COLUMN storage_policy"
        )
        session.execute("ALTER TABLE storage_clusters DROP COLUMN capacity_info")


migration_step = MigrationStep()
