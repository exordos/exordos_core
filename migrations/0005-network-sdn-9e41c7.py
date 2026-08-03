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

# The SDN control-plane API (docs/network/realm-connectivity-sdn.md):
#
#   * machine_pools.hypervisor_node — whose agent wires a pool's guests.
#   * compute_networks.access       — the tenancy boundary; partial index on
#     `public` rows keeps the visibility filter cheap.
#   * compute_ports.config          — the polymorphic port kind, plus the
#     port_security anti-spoof toggle.
#   * net_security_groups, net_identity_groups, net_addresses — catalog
#     reference objects.
#   * net_nfs                       — network functions, with the subnet or
#     network they were seeded for.
#   * net_proxy_services            — doors from an overlay network onto
#     services of the installation.
#   * nodes/compute_sets.interfaces — NULL means the node declared nothing
#     (the old placement rule); an empty list asks for no networking.
#   * compute_ports.index/provenance — device order (0 is the primary leg)
#     and who owns the port (`generated` by the node's declaration, `user`).
#   * the network.* permissions (three-part `service.resource.action`, as the
#     IAM enforcer parses them), bound to the owner role except `PRIVILEGED`.
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
    ("network.proxy_service.read", "List and read proxy doors"),
    ("network.proxy_service.create", "Open a proxy door onto a service"),
    ("network.proxy_service.update", "Update proxy doors"),
    ("network.proxy_service.delete", "Close proxy doors"),
    (
        "network.proxy_service.custom_destination",
        "Open a door onto a bare address, or on a shared network",
    ),
    ("network.network.share", "Publish a network to other projects (access=public)"),
    (
        "compute.node.foreign_subnet",
        "Declare a node leg in a subnet of another project",
    ),
    (
        "compute.node_set.foreign_subnet",
        "Declare a node-set leg in a subnet of another project",
    ),
    (
        "network.address.explicit_address",
        "Pin a specific address in a shared network",
    ),
)


# Not bound to the owner role: every project creator holds it, so it would
# gate nothing. Granted explicitly (or via the admin wildcard).
PRIVILEGED = frozenset(
    {
        "network.network.share",
        "compute.node.foreign_subnet",
        "compute.node_set.foreign_subnet",
        "network.address.explicit_address",
        "network.proxy_service.custom_destination",
    }
)


def _u(name: str) -> str:
    return str(sys_uuid.uuid5(NS_UUID, name))


COMPUTE_PROJECT_UUID = _u("GenesisCore-Compute-Project")

# The view has no reader left; a downgrade puts it back.
_NODES_WITHOUT_PORTS_VIEW = """\
CREATE VIEW public.compute_nodes_without_ports AS
 SELECT nodes.uuid,
    nodes.project_id,
    nodes.name,
    nodes.description,
    nodes.cores,
    nodes.ram,
    nodes.node_type,
    nodes.status,
    nodes.created_at,
    nodes.updated_at,
    nodes.default_network,
    nodes.node_set,
    nodes.placement_policies,
    nodes.disk_spec,
    nodes.hostname
   FROM (public.nodes
     LEFT JOIN public.compute_ports ports ON ((nodes.uuid = ports.node)))
  WHERE (ports.uuid IS NULL)"""


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0004-add-opaque-secrets-362f6d21.py"]

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
    -- The bit of the mark the fabric carries for the group; unique per
    -- network, because a packet never crosses from one to another.
    identity INTEGER NOT NULL,
    UNIQUE (network, identity)
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
CREATE UNIQUE INDEX net_addresses_owner_port_idx
    ON net_addresses(owner_port) WHERE owner_port IS NOT NULL;
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
    owner_subnet UUID,
    owner_network UUID
);

CREATE INDEX ON net_nfs(project_id, name);
CREATE INDEX ON net_nfs(provenance);

-- A generated service is a singleton of its owner: one dhcp per subnet, one
-- dns and one proxy per network, one default group per subnet. The seeding is
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
""",
            """\
CREATE TABLE net_proxy_services (
    uuid UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    project_id UUID NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    network UUID NOT NULL,
    port INTEGER NOT NULL,
    destination JSONB NOT NULL,
    domain VARCHAR(255)
);

CREATE INDEX ON net_proxy_services(project_id, name);

-- One door per port per network. The compiler maps a port to exactly one
-- upstream, so a second row on the same pair is not a merge to make: it is
-- one service answering where another one's clients are pointed, decided by
-- whichever row a query happened to return first. Refused in the model as
-- well; this is what keeps two racing creates from both getting through.
CREATE UNIQUE INDEX net_proxy_services_network_port_idx
    ON net_proxy_services(network, port);
""",
            """\
CREATE INDEX IF NOT EXISTS compute_networks_public_idx
    ON compute_networks(access) WHERE access = 'public';
""",
            "ALTER TABLE nodes ADD COLUMN interfaces JSONB[]",
            "ALTER TABLE compute_sets ADD COLUMN interfaces JSONB[]",
            "ALTER TABLE compute_ports ADD COLUMN index INTEGER NOT NULL DEFAULT 0",
            # Existing ports were all allocated by placement: `generated`.
            (
                "ALTER TABLE compute_ports ADD COLUMN provenance "
                "VARCHAR(16) NOT NULL DEFAULT 'generated'"
            ),
            # Number existing ports per node in creation order (the first is
            # the one whose address the node took), or the unique index below
            # fails on multi-port bare-metal nodes.
            (
                "UPDATE compute_ports p SET index = o.index FROM ("
                "SELECT uuid, row_number() OVER ("
                "PARTITION BY node ORDER BY created_at, uuid) - 1 AS index "
                "FROM compute_ports WHERE node IS NOT NULL) o "
                "WHERE p.uuid = o.uuid"
            ),
            # One port per (node, index), so racing compiler passes cannot
            # both claim the primary slot.
            (
                "CREATE UNIQUE INDEX compute_ports_node_index_idx "
                "ON compute_ports(node, index) WHERE node IS NOT NULL"
            ),
            "DROP VIEW IF EXISTS public.compute_nodes_without_ports",
            # A target resource is rebuilt only when the row's `updated_at`
            # moves past `tracked_at`; a code change moves neither. Touch the
            # rows so every pool is re-rendered by this release.
            """\
UPDATE machine_pools SET updated_at = now();
""",
            # Same for a removed field: stored borders still carry `routes`,
            # which the data plane no longer hashes, so the hashes would
            # disagree for ever and every border be re-applied each pass.
            """\
UPDATE net_border SET updated_at = now();
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
            if name in PRIVILEGED:
                continue
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
            "DROP INDEX IF EXISTS compute_ports_node_index_idx;",
            "ALTER TABLE compute_ports DROP COLUMN IF EXISTS provenance;",
            "ALTER TABLE compute_ports DROP COLUMN IF EXISTS index;",
            "ALTER TABLE compute_sets DROP COLUMN IF EXISTS interfaces;",
            "ALTER TABLE nodes DROP COLUMN IF EXISTS interfaces;",
            _NODES_WITHOUT_PORTS_VIEW,
            "DROP TABLE IF EXISTS net_proxy_services;",
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
