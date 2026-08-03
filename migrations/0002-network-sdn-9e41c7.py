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

# The SDN control-plane API in one step (docs/network/sdn-cp-api-spec.md):
# the schema this feature ends up with, rather than the sequence of shapes it
# passed through while it was being written.
#
#   * machine_pools.hypervisor_node — whose agent wires a pool's guests.
#   * compute_networks.access       — the tenancy boundary (§3), with a
#     partial index on the `public` rows so the shared-network visibility
#     filter (project OR access=public) stays cheap as networks grow (§8).
#   * compute_ports.config          — the polymorphic thin-port kind (§5),
#     plus the port_security anti-spoof toggle.
#   * net_security_groups, net_addresses — catalog reference objects (§6).
#   * net_nfs                       — the network functions (§7), each with
#     the owner it exists for: `owner_port` for what a `simple` port emits
#     (§5.1), `owner_subnet`/`owner_network` for the services the
#     installation seeds for a subnet (dhcp) and a network (dns, proxy).
#   * the network.* permissions, bound to the compute project's owner role.
#     All in the three-part `service.resource.action` form the IAM enforcer
#     actually parses (`str.split('.', maxsplit=2)`); `network.network.share`
#     and `network.address.explicit_address` gate the multi-tenant HTTP path
#     (§8).
NS_UUID = sys_uuid.UUID("dfd0c604-607f-4260-981f-374f88435ea0")
OWNER_ROLE_UUID = "726f6c65-0000-0000-0000-000000000002"

PERMISSIONS = (
    ("network.network.read", "List and read overlay/flat networks"),
    ("network.network.create", "Create overlay/flat networks"),
    ("network.network.update", "Update networks"),
    ("network.network.delete", "Delete networks"),
    ("network.subnet.read", "List and read subnets"),
    ("network.subnet.create", "Create subnets"),
    ("network.subnet.update", "Update subnets"),
    ("network.subnet.delete", "Delete subnets"),
    ("network.port.read", "List and read ports"),
    ("network.port.create", "Create ports"),
    ("network.port.update", "Update ports"),
    ("network.port.delete", "Delete ports"),
    ("network.security_group.read", "List and read security groups"),
    ("network.security_group.create", "Create security groups"),
    ("network.security_group.update", "Update security groups"),
    ("network.security_group.delete", "Delete security groups"),
    ("network.identity_group.read", "List and read identity groups"),
    ("network.identity_group.create", "Create identity groups"),
    ("network.identity_group.update", "Update identity groups"),
    ("network.identity_group.delete", "Delete identity groups"),
    ("network.address.read", "List and read allocated addresses"),
    ("network.address.create", "Allocate addresses"),
    ("network.address.update", "Associate/disassociate addresses"),
    ("network.address.delete", "Release addresses"),
    ("network.nf.read", "List and read network functions"),
    ("network.nf.update", "Update network functions"),
    ("network.nf.delete", "Delete network functions"),
    ("network.network.share", "Publish a network to other projects (access=public)"),
    (
        "network.address.explicit_address",
        "Pin a specific address in a shared network",
    ),
)


def _u(name: str) -> str:
    return str(sys_uuid.uuid5(NS_UUID, name))


COMPUTE_PROJECT_UUID = _u("GenesisCore-Compute-Project")


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0001-dns-record-tags-4c81de.py"]

    @property
    def migration_id(self):
        return "9e41c700-6f3a-4c2d-8b17-5a0e2d9f4b63"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        expressions = [
            """\
ALTER TABLE machine_pools
    ADD COLUMN IF NOT EXISTS hypervisor_node UUID DEFAULT NULL;
""",
            """\
ALTER TABLE compute_networks
    ADD COLUMN IF NOT EXISTS access VARCHAR(32) NOT NULL DEFAULT 'private';
""",
            """\
ALTER TABLE compute_ports
    ADD COLUMN IF NOT EXISTS config JSONB NOT NULL DEFAULT '{"kind": "simple"}',
    ADD COLUMN IF NOT EXISTS port_security BOOLEAN NOT NULL DEFAULT TRUE;
""",
            """\
ALTER TABLE compute_subnets
    ADD COLUMN IF NOT EXISTS placeable BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS shared_pool BOOLEAN NOT NULL DEFAULT FALSE;
""",
            """\
CREATE TABLE net_security_groups (
    uuid UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    project_id UUID NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    rules JSONB[] NOT NULL DEFAULT '{}'
);

CREATE INDEX ON net_security_groups(project_id, name);
""",
            """\
CREATE TABLE net_identity_groups (
    uuid UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    project_id UUID NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    network UUID NOT NULL,
    -- One of the two, never both: a bit of the mark the fabric carries
    -- while its network still has one, or the number a conjunctive match
    -- joins the group's members to the rules naming it. The bit is unique
    -- per network (a packet never crosses from one to another); the join
    -- number is unique everywhere, because every VNI on a host shares one
    -- br-int and two sets meeting under one number would be one set.
    identity INTEGER,
    conj_id INTEGER,
    UNIQUE (network, identity),
    UNIQUE (conj_id),
    CONSTRAINT net_identity_groups_carried_one_way CHECK (
        (identity IS NULL) <> (conj_id IS NULL)
    )
);

CREATE INDEX ON net_identity_groups(project_id, name);
""",
            """\
CREATE TABLE net_addresses (
    uuid UUID PRIMARY KEY,
    project_id UUID NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    subnet UUID NOT NULL,
    address VARCHAR(45) NOT NULL,
    allocation VARCHAR(16) NOT NULL DEFAULT 'reserved',
    origin VARCHAR(16) NOT NULL DEFAULT 'explicit',
    owner_port UUID,
    association UUID
);

CREATE INDEX ON net_addresses(project_id);
CREATE INDEX ON net_addresses(association);

-- One live claim per address, and only the live ones. A freed row is the
-- receipt of an address that went back to its subnet's pool, so it must not
-- keep the next caller from reserving that address — while two *reserved*
-- rows on one address is exactly the collision the allocator's subnet lock
-- exists to prevent, caught here as well in case anything reaches the table
-- by another road.
CREATE UNIQUE INDEX net_addresses_reserved_idx
    ON net_addresses(subnet, address) WHERE allocation = 'reserved';
""",
            """\
CREATE TABLE net_nfs (
    uuid UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    project_id UUID NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    kind VARCHAR(32) NOT NULL,
    config JSONB NOT NULL DEFAULT '{}',
    provenance VARCHAR(16) NOT NULL DEFAULT 'user',
    owner_port UUID,
    owner_subnet UUID,
    owner_network UUID
);

CREATE INDEX ON net_nfs(project_id, name);
CREATE INDEX ON net_nfs(provenance);
CREATE INDEX net_nfs_owner_port_idx ON net_nfs(owner_port);
CREATE INDEX net_nfs_owner_subnet_idx ON net_nfs(owner_subnet);
CREATE INDEX net_nfs_owner_network_idx ON net_nfs(owner_network);

-- A generated service is a singleton of its owner: one dhcp per subnet, one
-- dns and one proxy per network, one splitter per port. The seeding is
-- idempotent by lookup, which is not the same as unique — two compile passes
-- that raced both inserted, and afterwards the reader picked whichever row
-- the query returned first. The slice it schedules is keyed by uuid5 of that
-- row, so the winner flipping between iterations made the host install and
-- collect the same function for ever. This is what makes the loser of the
-- race an integrity error the seeder already catches, rather than a second
-- truth.
CREATE UNIQUE INDEX net_nfs_owner_subnet_kind_idx
    ON net_nfs(owner_subnet, kind) WHERE owner_subnet IS NOT NULL;
CREATE UNIQUE INDEX net_nfs_owner_network_kind_idx
    ON net_nfs(owner_network, kind) WHERE owner_network IS NOT NULL;
CREATE UNIQUE INDEX net_nfs_owner_port_kind_idx
    ON net_nfs(owner_port, kind) WHERE owner_port IS NOT NULL;
""",
            """\
CREATE INDEX IF NOT EXISTS compute_networks_public_idx
    ON compute_networks(access) WHERE access = 'public';
""",
            # `LibvirtPoolDriverSpec` gains `ovs`, and a target resource is
            # only rebuilt from its model when the row's `updated_at` moves
            # past `ua_target_resources.tracked_at` (see
            # `get_updated_entities`). A field added in code moves neither,
            # so on an upgrade the control plane keeps sending the old spec
            # while the agent -- already on the new code -- reports the new
            # one. The two hashes then never agree and the pool is updated
            # on every iteration, for ever. Bumping `updated_at` is what
            # tells the builder to re-emit the target in the new shape.
            #
            # This is the general rule for adding a property to anything
            # serialised into a target resource: touch the rows, or they
            # stay desynchronised until someone edits them by hand.
            """\
UPDATE machine_pools SET updated_at = now();
""",
        ]

        for expression in expressions:
            session.execute(expression, None)

        for name, description in PERMISSIONS:
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
        for name, _ in PERMISSIONS:
            session.execute(f"""
                DELETE FROM iam_binding_permissions
                WHERE permission = '{_u(name)}';
            """)
            session.execute(f"""
                DELETE FROM iam_permissions
                WHERE uuid = '{_u(name)}';
            """)

        for expression in (
            "DROP INDEX IF EXISTS compute_networks_public_idx;",
            "DROP TABLE IF EXISTS net_nfs;",
            "DROP TABLE IF EXISTS net_addresses;",
            "DROP TABLE IF EXISTS net_security_groups;",
            "DROP TABLE IF EXISTS net_identity_groups;",
            "ALTER TABLE compute_subnets DROP COLUMN IF EXISTS shared_pool;",
            "ALTER TABLE compute_subnets DROP COLUMN IF EXISTS placeable;",
            "ALTER TABLE compute_ports DROP COLUMN IF EXISTS port_security;",
            "ALTER TABLE compute_ports DROP COLUMN IF EXISTS config;",
            "ALTER TABLE compute_networks DROP COLUMN IF EXISTS access;",
            "ALTER TABLE machine_pools DROP COLUMN IF EXISTS hypervisor_node;",
        ):
            session.execute(expression, None)


migration_step = MigrationStep()
