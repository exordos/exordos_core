# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0

from restalchemy.storage.sql import migrations


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0012-evpn-egress-policy-c3a4f1.py"]

    @property
    def migration_id(self):
        return "74db8134-36c3-4889-94e3-9a552db0ba19"

    def upgrade(self, session):
        session.execute(
            "ALTER TABLE machine_pools ADD COLUMN project_id UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000'"
        )
        session.execute(
            "ALTER TABLE machine_pools ADD COLUMN driver_state JSONB NOT NULL DEFAULT '{}'"
        )
        session.execute(
            "ALTER TABLE machine_pools ADD COLUMN network_defaults JSONB NOT NULL DEFAULT '{}'"
        )
        session.execute("""CREATE TABLE cloud_network_links (
            uuid UUID PRIMARY KEY, project_id UUID NOT NULL,
            name VARCHAR(255) NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL,
            endpoints JSONB NOT NULL, transport JSONB NOT NULL,
            mtu INTEGER NOT NULL DEFAULT 1380,
            secrets JSONB NOT NULL DEFAULT '{}', state JSONB NOT NULL DEFAULT '{}',
            recovery JSONB NOT NULL DEFAULT '{}',
            status VARCHAR(32) NOT NULL DEFAULT 'NEW'
        )""")
        session.execute("""CREATE TABLE cloud_link_endpoints (
            uuid UUID PRIMARY KEY,
            link UUID NOT NULL REFERENCES cloud_network_links(uuid),
            subnet UUID NOT NULL UNIQUE REFERENCES compute_subnets(uuid),
            connection UUID REFERENCES machine_pools(uuid),
            definition JSONB NOT NULL
        )""")
        session.execute(
            "CREATE INDEX cloud_link_endpoint_link ON cloud_link_endpoints(link)"
        )
        session.execute(
            "UPDATE ua_target_resources SET updated_at = NOW() WHERE kind = 'pool_machine'"
        )

    def downgrade(self, session):
        session.execute("DROP TABLE cloud_link_endpoints")
        session.execute("DROP TABLE cloud_network_links")
        session.execute("ALTER TABLE machine_pools DROP COLUMN network_defaults")
        session.execute("ALTER TABLE machine_pools DROP COLUMN driver_state")
        session.execute("ALTER TABLE machine_pools DROP COLUMN project_id")


migration_step = MigrationStep()
