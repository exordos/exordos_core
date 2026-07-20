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

import uuid as sys_uuid

from restalchemy.storage.sql import migrations

# Same namespace/role/project as 0053 (network.lb permissions): border is
# the second network resource driven by project owners (and, via manifest
# bindings, by the ecosystem's service user for managed realms).
NS_UUID = sys_uuid.UUID("dfd0c604-607f-4260-981f-374f88435ea0")
OWNER_ROLE_UUID = "726f6c65-0000-0000-0000-000000000002"

BORDER_PERMISSIONS = (
    ("network.border.read", "List and read borders (NAT gateways)"),
    ("network.border.create", "Create borders (NAT gateways)"),
    ("network.border.update", "Update borders (NAT gateways)"),
    ("network.border.delete", "Delete borders (NAT gateways)"),
)


def _u(name: str) -> str:
    return str(sys_uuid.uuid5(NS_UUID, name))


COMPUTE_PROJECT_UUID = _u("GenesisCore-Compute-Project")


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0066-machine-pools-driver-spec-kind-migration-138d02.py"]

    @property
    def migration_id(self):
        return "ec37b4c7-5218-40af-a8cb-c00e78c16809"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        # A border is machine-managed and reconciled as one flat resource:
        # SNAT/DNAT rules are carried inline (restalchemy `types.List()`
        # maps to a postgres jsonb[] array, like net_lb.external_sources).
        # `type` is the deployment shape, calque of net_lb.type: core_agent
        # (default, core node's agent) or core (dedicated VM); an explicitly
        # pinned `node` keeps winning over `type`. `ipsv4` are the public
        # IPv4s of a `core` border VM (like net_lb.ipsv4); NOT NULL +
        # default: a NULL breaks TypedList restore on pre-existing rows.
        expressions = [
            """\
CREATE TABLE net_border (
    uuid UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    project_id UUID NOT NULL,
    status VARCHAR(64) NOT NULL DEFAULT 'NEW',
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    node UUID,
    snat_rules JSONB[] NOT NULL DEFAULT '{}',
    forwards JSONB[] NOT NULL DEFAULT '{}',
    type JSONB NOT NULL DEFAULT '{"kind": "core_agent"}',
    ipsv4 varchar(15)[] NOT NULL DEFAULT '{}'
);

CREATE INDEX ON net_border(project_id, name);
""",
        ]

        for expression in expressions:
            session.execute(expression, None)

        for name, description in BORDER_PERMISSIONS:
            session.execute(f"""
                INSERT INTO iam_permissions (
                    uuid, name, description
                ) VALUES (
                    '{_u(name)}',
                    '{name}',
                    '{description}'
                )
                ON CONFLICT (uuid) DO NOTHING;
            """)
            session.execute(f"""
                INSERT INTO iam_binding_permissions (
                    uuid, role, permission, project_id
                ) VALUES (
                    '{_u("binding." + name)}',
                    '{OWNER_ROLE_UUID}',
                    '{_u(name)}',
                    '{COMPUTE_PROJECT_UUID}'
                )
                ON CONFLICT (uuid) DO NOTHING;
            """)

    def downgrade(self, session):
        for name, _ in BORDER_PERMISSIONS:
            session.execute(f"""
                DELETE FROM iam_binding_permissions
                WHERE permission = '{_u(name)}';
            """)
            session.execute(f"""
                DELETE FROM iam_permissions
                WHERE uuid = '{_u(name)}';
            """)

        self._delete_table_if_exists(session, "net_border")


migration_step = MigrationStep()
