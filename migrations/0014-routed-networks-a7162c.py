# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0

from restalchemy.storage.sql import migrations


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0013-cloud-connections-74db81.py"]

    @property
    def migration_id(self):
        return "a7162c44-7cad-43e6-86a6-eeb192e7f655"

    def upgrade(self, session):
        session.execute(
            "ALTER TABLE machine_pools ADD COLUMN site VARCHAR(255) DEFAULT NULL"
        )
        session.execute(
            "ALTER TABLE compute_networks ADD COLUMN owner_network UUID REFERENCES compute_networks(uuid)"
        )
        session.execute(
            "ALTER TABLE compute_networks ADD COLUMN cidr VARCHAR(64) DEFAULT NULL"
        )
        session.execute(
            "ALTER TABLE compute_networks ADD COLUMN connectivity JSONB NOT NULL DEFAULT '{}'"
        )
        session.execute(
            "ALTER TABLE compute_subnets ADD COLUMN logical_network UUID REFERENCES compute_networks(uuid)"
        )
        session.execute(
            "ALTER TABLE compute_subnets ADD COLUMN hypervisors JSONB NOT NULL DEFAULT '[]'"
        )
        session.execute(
            "ALTER TABLE compute_subnets ADD COLUMN gateway JSONB NOT NULL DEFAULT '{}'"
        )
        session.execute(
            "ALTER TABLE compute_subnets ADD COLUMN status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE'"
        )
        session.execute(
            "ALTER TABLE compute_subnets ADD COLUMN error TEXT NOT NULL DEFAULT ''"
        )
        session.execute(
            "ALTER TABLE compute_sets ADD COLUMN placement JSONB DEFAULT NULL"
        )
        session.execute(
            "ALTER TABLE compute_placement_policies ADD COLUMN scope VARCHAR(16) NOT NULL DEFAULT 'pool'"
        )
        session.execute("""CREATE TABLE network_segments (
            uuid UUID PRIMARY KEY, project_id UUID NOT NULL,
            name VARCHAR(255) NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL,
            network UUID NOT NULL REFERENCES compute_networks(uuid),
            local_network UUID NOT NULL REFERENCES compute_networks(uuid),
            subnet UUID NOT NULL UNIQUE REFERENCES compute_subnets(uuid) DEFERRABLE INITIALLY DEFERRED,
            definition JSONB NOT NULL, status VARCHAR(32) NOT NULL DEFAULT 'IN_PROGRESS',
            error TEXT NOT NULL DEFAULT ''
        )""")
        session.execute(
            "ALTER TABLE cloud_network_links ADD COLUMN owner_network UUID REFERENCES compute_networks(uuid)"
        )

    def downgrade(self, session):
        session.execute("ALTER TABLE cloud_network_links DROP COLUMN owner_network")
        session.execute("DROP TABLE network_segments")
        for column in ("logical_network", "hypervisors", "gateway", "status", "error"):
            session.execute(f"ALTER TABLE compute_subnets DROP COLUMN {column}")
        session.execute("ALTER TABLE compute_networks DROP COLUMN connectivity")
        session.execute("ALTER TABLE compute_networks DROP COLUMN cidr")
        session.execute("ALTER TABLE compute_networks DROP COLUMN owner_network")
        session.execute("ALTER TABLE compute_placement_policies DROP COLUMN scope")
        session.execute("ALTER TABLE compute_sets DROP COLUMN placement")
        session.execute("ALTER TABLE machine_pools DROP COLUMN site")


migration_step = MigrationStep()
