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
        session.execute("""CREATE TABLE cloud_connections (
            uuid UUID PRIMARY KEY, project_id UUID NOT NULL,
            name VARCHAR(255) NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL,
            cloud VARCHAR(255) NOT NULL, driver VARCHAR(255) NOT NULL DEFAULT 'openstack',
            region VARCHAR(255), orch_endpoint VARCHAR(2048) NOT NULL,
            status_endpoint VARCHAR(2048) NOT NULL, agent_package VARCHAR(2048) NOT NULL
        )""")
        session.execute(
            "CREATE INDEX cloud_connections_project ON cloud_connections(project_id)"
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
            connection UUID REFERENCES cloud_connections(uuid),
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
        session.execute("DROP TABLE cloud_connections")


migration_step = MigrationStep()
