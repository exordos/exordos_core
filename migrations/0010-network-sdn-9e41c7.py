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

import json
import uuid as sys_uuid

from gcl_sdk.agents.universal import utils as ua_utils

from restalchemy.storage.sql import migrations

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
        "network.address.explicit_address",
        "Pin a specific address in a shared network",
    ),
)


# Not bound to the owner role: every project creator holds it, so it would
# gate nothing. Granted explicitly (or via the admin wildcard).
PERMISSIONS += tuple(
    (f"network.{resource}.{action}", f"{action.capitalize()} {resource}")
    for resource in ("security_group_rule", "port_security_group")
    for action in ("read", "create", "update", "delete")
) + (("network.subnet.floating_pool", "Manage floating address pools"),)

PRIVILEGED = frozenset(
    {
        "network.network.share",
        "network.subnet.floating_pool",
        "network.address.explicit_address",
        "network.proxy_service.custom_destination",
    }
)


def _u(name: str) -> str:
    return str(sys_uuid.uuid5(NS_UUID, name))


COMPUTE_PROJECT_UUID = _u("GenesisCore-Compute-Project")

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


_POOL_PORTS_VIEW = """
CREATE VIEW compute_pool_ports AS
SELECT p.uuid, p.project_id, p.created_at,
       GREATEST(p.updated_at, m.updated_at) AS updated_at,
       m.uuid AS machine, m.pool, m.boot, p.mac, p.index,
       COALESCE(n.driver_spec->>'driver' = 'ovs_evpn', false) AS overlay,
       CASE WHEN p.index = 0 AND m.boot = 'network'
                 AND m.name NOT LIKE 'core-set-node%'
                 AND n.driver_spec->>'driver' = 'flat_bridge'
            THEN (SELECT min(name) FROM compute_subnets WHERE next_server IS NOT NULL HAVING count(*) = 1)
            ELSE COALESCE(p.source, s.name) END AS source,
       true AS attached, 'ACTIVE'::varchar AS status
FROM compute_ports p
JOIN machines m ON m.node = p.node
JOIN compute_subnets s ON s.uuid = p.subnet
JOIN compute_networks n ON n.uuid = s.network
WHERE p.mac IS NOT NULL AND m.pool IS NOT NULL
"""


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0009-add-managed-token-metadata-8c42f019.py"]

    @property
    def migration_id(self):
        return "9e41c700-6f3a-4c2d-8b17-5a0e2d9f4b63"

    def upgrade(self, session):
        for expression in (
            "ALTER TABLE machine_pools ADD COLUMN hypervisor_node UUID",
            "ALTER TABLE compute_networks ADD COLUMN access VARCHAR(32) NOT NULL DEFAULT 'private', "
            "ADD COLUMN driver_state JSONB NOT NULL DEFAULT '{}', "
            "ADD COLUMN is_default BOOLEAN NOT NULL DEFAULT FALSE",
            "CREATE INDEX compute_networks_public_idx ON compute_networks(access) WHERE access = 'public'",
            "CREATE UNIQUE INDEX compute_networks_default_project_idx ON compute_networks(project_id) WHERE is_default",
            "ALTER TABLE compute_subnets ADD COLUMN placeable BOOLEAN NOT NULL DEFAULT TRUE, "
            "ADD COLUMN shared_pool BOOLEAN NOT NULL DEFAULT FALSE, "
            "ADD COLUMN floating_pool BOOLEAN NOT NULL DEFAULT FALSE, "
            "ADD COLUMN default_floating_pool BOOLEAN NOT NULL DEFAULT FALSE, "
            "ADD CONSTRAINT default_floating_pool_valid CHECK (NOT default_floating_pool OR (floating_pool AND NOT placeable))",
            "CREATE UNIQUE INDEX compute_subnets_default_floating_project_idx ON compute_subnets(project_id) WHERE default_floating_pool",
            "ALTER TABLE compute_ports ADD COLUMN port_security BOOLEAN NOT NULL DEFAULT TRUE, "
            "ADD COLUMN enforce_security_group BOOLEAN NOT NULL DEFAULT FALSE, "
            "ADD COLUMN index INTEGER, ADD COLUMN owner_node UUID, "
            "ADD COLUMN target_mac VARCHAR(17), ADD COLUMN dhcp BOOLEAN, ADD COLUMN dns BOOLEAN",
            # Released ports were created by Node placement. Preserve their identity
            # and assign the primary slot by creation order, including bare metal.
            "UPDATE compute_ports p SET owner_node = p.node, index = o.index FROM ("
            "SELECT uuid, row_number() OVER (PARTITION BY node ORDER BY created_at, uuid) - 1 AS index "
            "FROM compute_ports WHERE node IS NOT NULL) o WHERE p.uuid = o.uuid",
            "CREATE UNIQUE INDEX compute_ports_node_index_idx ON compute_ports(node, index) WHERE node IS NOT NULL",
            "CREATE INDEX compute_ports_owner_node_idx ON compute_ports(owner_node) WHERE owner_node IS NOT NULL",
            "ALTER TABLE compute_sets ALTER COLUMN default_network DROP NOT NULL",
            "UPDATE compute_sets SET default_network = NULL WHERE default_network = '{}'::jsonb",
            # Released Nodes stored Port observations here, not a creation template.
            "UPDATE nodes SET default_network = NULL WHERE default_network::jsonb ? 'port'",
            "DROP VIEW public.compute_nodes_without_ports",
            """
            CREATE TABLE net_security_groups (
                uuid UUID PRIMARY KEY, name VARCHAR(255) NOT NULL, description TEXT,
                project_id UUID NOT NULL, created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            );
            CREATE INDEX ON net_security_groups(project_id, name);
            CREATE TABLE net_security_group_rules (
                uuid UUID PRIMARY KEY, project_id UUID NOT NULL,
                created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL,
                security_group UUID NOT NULL REFERENCES net_security_groups(uuid),
                direction VARCHAR(16) NOT NULL,
                protocol VARCHAR(16) NOT NULL DEFAULT 'any', port INTEGER,
                remote_ip VARCHAR(43), remote_group_id UUID REFERENCES net_security_groups(uuid),
                CHECK (direction IN ('ingress', 'egress')),
                CHECK (protocol IN ('any', 'tcp', 'udp', 'icmp')),
                CHECK (port IS NULL OR (port BETWEEN 1 AND 65535 AND protocol IN ('tcp', 'udp'))),
                CHECK (remote_ip IS NULL OR remote_group_id IS NULL),
                CHECK (remote_group_id IS NULL OR direction = 'ingress')
            );
            CREATE INDEX ON net_security_group_rules(project_id, security_group);
            CREATE TABLE net_security_group_identities (
                uuid UUID PRIMARY KEY,
                network UUID NOT NULL REFERENCES compute_networks(uuid) ON DELETE CASCADE,
                security_group UUID NOT NULL REFERENCES net_security_groups(uuid) ON DELETE CASCADE,
                identity INTEGER NOT NULL CHECK (identity BETWEEN 1 AND 32768 AND (identity & (identity - 1)) = 0),
                UNIQUE (network, security_group), UNIQUE (network, identity)
            );
            CREATE TABLE net_port_security_groups (
                uuid UUID PRIMARY KEY, project_id UUID NOT NULL,
                created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL,
                port UUID NOT NULL REFERENCES compute_ports(uuid),
                security_group UUID NOT NULL REFERENCES net_security_groups(uuid),
                UNIQUE (port, security_group)
            );
            CREATE INDEX ON net_port_security_groups(project_id);
            CREATE TABLE net_addresses (
                uuid UUID PRIMARY KEY, project_id UUID NOT NULL,
                created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL,
                subnet UUID NOT NULL, address VARCHAR(45) NOT NULL,
                kind VARCHAR(16) NOT NULL DEFAULT 'reservation',
                port UUID REFERENCES compute_ports(uuid), owner_port UUID,
                association UUID
            );
            CREATE INDEX ON net_addresses(project_id);
            CREATE INDEX ON net_addresses(association);
            CREATE UNIQUE INDEX ON net_addresses(port) WHERE port IS NOT NULL;
            CREATE UNIQUE INDEX net_addresses_owner_port_idx ON net_addresses(owner_port) WHERE owner_port IS NOT NULL;
            CREATE UNIQUE INDEX net_addresses_reserved_idx ON net_addresses(subnet, address);
            CREATE UNIQUE INDEX net_addresses_floating_reserved_idx ON net_addresses(address) WHERE kind = 'floating';
            CREATE TABLE net_nfs (
                uuid UUID PRIMARY KEY, name VARCHAR(255) NOT NULL, description TEXT,
                project_id UUID NOT NULL, created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL, kind VARCHAR(32) NOT NULL,
                config JSONB NOT NULL DEFAULT '{}', owner_subnet UUID, owner_network UUID
            );
            CREATE INDEX ON net_nfs(project_id, name);
            CREATE UNIQUE INDEX net_nfs_owner_subnet_kind_idx ON net_nfs(owner_subnet, kind) WHERE owner_subnet IS NOT NULL;
            CREATE UNIQUE INDEX net_nfs_owner_network_kind_idx ON net_nfs(owner_network, kind) WHERE owner_network IS NOT NULL;
            CREATE TABLE net_proxy_services (
                uuid UUID PRIMARY KEY, name VARCHAR(255) NOT NULL, description TEXT,
                project_id UUID NOT NULL, created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL, network UUID NOT NULL,
                port INTEGER NOT NULL, destination JSONB NOT NULL, domain VARCHAR(255)
            );
            CREATE INDEX ON net_proxy_services(project_id, name);
            CREATE UNIQUE INDEX net_proxy_services_network_port_idx ON net_proxy_services(network, port);
            """,
        ):
            session.execute(expression)
        session.execute(
            "UPDATE machine_pools "
            "SET driver_spec = jsonb_set(driver_spec, '{ovs}', 'false'::jsonb), "
            "updated_at = NOW() "
            "WHERE driver_spec->>'kind' IN ('libvirt', 'exordos_local_hyper') "
            "AND NOT (driver_spec ? 'ovs')"
        )
        self.repair_flat_sources(session)
        session.execute(_POOL_PORTS_VIEW)
        for resource in session.execute(
            "SELECT res_uuid, value FROM ua_target_resources WHERE kind = 'pool_machine'"
        ).fetchall():
            value = dict(resource["value"])
            value.pop("port_info", None)
            value.pop("ports_info", None)
            self.write_target(session, resource["res_uuid"], value)
        # Builders must regenerate resources whose wire projection changed.
        for table in ("nodes", "compute_sets", "machine_pools", "net_border"):
            session.execute(f"UPDATE {table} SET updated_at = NOW()")
        for name, description in PERMISSIONS:
            session.execute(
                "INSERT INTO iam_permissions (uuid, name, description) VALUES (%s, %s, %s)",
                (_u(name), name, description),
            )
            if name not in PRIVILEGED:
                session.execute(
                    "INSERT INTO iam_binding_permissions (uuid, role, permission, project_id) VALUES (%s, %s, %s, %s)",
                    (
                        _u("binding." + name),
                        OWNER_ROLE_UUID,
                        _u(name),
                        COMPUTE_PROJECT_UUID,
                    ),
                )

    @staticmethod
    def repair_flat_sources(session):
        session.execute(
            """WITH repaired AS (
                UPDATE compute_ports AS port
                SET source = subnet.name, updated_at = NOW()
                FROM compute_subnets AS subnet
                JOIN compute_networks AS network ON network.uuid = subnet.network
                WHERE port.subnet = subnet.uuid AND port.source IS NULL
                  AND network.driver_spec->>'driver' = 'flat_bridge'
                RETURNING port.machine, port.node
            )
            UPDATE machines SET updated_at = NOW()
            WHERE uuid IN (SELECT machine FROM repaired)
               OR node IN (SELECT node FROM repaired)"""
        )

    @staticmethod
    def write_target(session, res_uuid, value):
        session.execute(
            "UPDATE ua_target_resources SET value = %s::jsonb, hash = %s, full_hash = '' "
            "WHERE res_uuid = %s",
            (json.dumps(value), ua_utils.calculate_hash(value), res_uuid),
        )

    def downgrade(self, session):
        for resource in session.execute(
            "SELECT t.res_uuid, t.value, jsonb_build_object("
            "'ipv4', p.ipv4, 'mask', p.mask, 'mac', p.mac, "
            "'source', COALESCE(v.source, p.source, s.name)) AS port_info "
            "FROM ua_target_resources t JOIN machines m ON m.uuid = t.uuid "
            "JOIN compute_ports p ON p.node = m.node AND p.index = 0 "
            "JOIN compute_subnets s ON s.uuid = p.subnet "
            "LEFT JOIN compute_pool_ports v ON v.uuid = p.uuid "
            "WHERE t.kind = 'pool_machine' AND NOT (t.value ? 'port_info')"
        ).fetchall():
            value = dict(resource["value"])
            value["port_info"] = resource["port_info"]
            self.write_target(session, resource["res_uuid"], value)
        session.execute("DELETE FROM ua_target_resources WHERE kind = 'pool_port'")
        session.execute("DELETE FROM ua_actual_resources WHERE kind = 'pool_port'")
        session.execute(
            "UPDATE machine_pools SET driver_spec = driver_spec - 'ovs', updated_at = NOW() "
            "WHERE driver_spec->>'kind' IN ('libvirt', 'exordos_local_hyper') "
            "AND driver_spec->'ovs' = 'false'::jsonb"
        )
        for resource in session.execute(
            "SELECT res_uuid, value FROM ua_target_resources WHERE kind = 'pool' "
            "AND value->'driver_spec'->>'kind' IN ('libvirt', 'exordos_local_hyper') "
            "AND value->'driver_spec'->'ovs' = 'false'::jsonb"
        ).fetchall():
            value = dict(resource["value"])
            value["driver_spec"].pop("ovs")
            self.write_target(session, resource["res_uuid"], value)
        for name, _ in PERMISSIONS:
            session.execute(
                "DELETE FROM iam_binding_permissions WHERE permission = %s", (_u(name),)
            )
            session.execute("DELETE FROM iam_permissions WHERE uuid = %s", (_u(name),))
        for expression in (
            "DROP VIEW compute_pool_ports",
            "UPDATE nodes n SET default_network = json_build_object("
            "'subnet', p.subnet, 'port', p.uuid, 'ipv4', p.ipv4, "
            "'target_ipv4', p.target_ipv4, 'mask', p.mask, 'mac', p.mac)::text "
            "FROM compute_ports p WHERE p.node = n.uuid AND p.index = 0",
            "DROP TABLE net_port_security_groups, net_security_group_rules, net_security_group_identities",
            "DROP TABLE net_proxy_services, net_nfs, net_addresses, net_security_groups",
            "DROP INDEX compute_ports_node_index_idx, compute_ports_owner_node_idx",
            "ALTER TABLE compute_ports DROP COLUMN port_security, DROP COLUMN enforce_security_group, "
            "DROP COLUMN index, DROP COLUMN owner_node, DROP COLUMN target_mac, DROP COLUMN dhcp, DROP COLUMN dns",
            "UPDATE compute_sets SET default_network = '{}'::jsonb WHERE default_network IS NULL",
            "ALTER TABLE compute_sets ALTER COLUMN default_network SET NOT NULL",
            "ALTER TABLE compute_subnets DROP COLUMN placeable, DROP COLUMN shared_pool, "
            "DROP COLUMN floating_pool, DROP COLUMN default_floating_pool",
            "ALTER TABLE compute_networks DROP COLUMN access, DROP COLUMN driver_state, DROP COLUMN is_default",
            "ALTER TABLE machine_pools DROP COLUMN hypervisor_node",
            _NODES_WITHOUT_PORTS_VIEW,
        ):
            session.execute(expression)


migration_step = MigrationStep()
