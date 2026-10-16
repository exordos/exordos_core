#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

import json
import uuid

from restalchemy.storage.sql import migrations

PERMISSIONS = tuple(
    f"network.{resource}.{action}"
    for resource in (
        "security_group_rule",
        "port_security_group",
        "port_identity_group",
    )
    for action in ("read", "create", "update", "delete")
)
PRIVILEGED_PERMISSIONS = ("network.subnet.floating_pool",)
PERMISSION_NAMESPACE = uuid.UUID("15df7a28-b649-4860-9dfa-8d98b2c07eac")


def permission_uuid(name):
    return str(uuid.uuid5(PERMISSION_NAMESPACE, name))


def canonical_interface(interface):
    """Move legacy toggle semantics to the interface's Port fields."""
    if not isinstance(interface, dict) or not isinstance(
        interface.get("config") or {}, dict
    ):
        raise ValueError("expand referenced interface configs before upgrading")
    value = dict(interface)
    config = dict(value.get("config") or {})
    for old, new in (
        ("dhcp", "dhcp"),
        ("dns", "dns"),
        ("subnet_group", "network_identity"),
    ):
        legacy = config.pop(old, None)
        if legacy is not None and not isinstance(legacy, bool):
            raise ValueError(
                "convert referenced config toggles to canonical fields before upgrading"
            )
        if legacy is False and not isinstance(value.get(new), bool):
            value[new] = False
    config.pop("security_groups", None)
    config.pop("identity_groups", None)
    if "config" in value:
        value["config"] = config
    return value


def networking_template(interfaces):
    """Carry group declarations into the canonical NodeSet creation template."""
    result = []
    for position, interface in enumerate(interfaces):
        if interface.get("kind") != "subnet":
            raise ValueError("convert NodeSet port-adoption templates before upgrading")
        normalized = canonical_interface(interface)
        port = {
            key: normalized[key]
            for key in (
                "subnet",
                "network",
                "dhcp",
                "dns",
                "network_identity",
                "port_security",
            )
            if normalized.get(key) is not None
        }
        if interface.get("address") is not None:
            raise ValueError("NodeSet templates require automatic private addresses")
        entry = {
            "port": port,
            "index": interface.get("index")
            if interface.get("index") is not None
            else position,
        }
        config = interface.get("config") or {}
        for key in ("security_groups", "identity_groups"):
            if config.get(key):
                entry[key] = config[key]
        public = config.get("public") or {}
        if public.get("address"):
            raise ValueError(
                "NodeSet templates require automatically allocated floating addresses"
            )
        if public.get("floating_from"):
            entry["floating_ip"] = {"subnet": public["floating_from"]}
        result.append(entry)
    return result


def migrate_interfaces(session):
    for table in ("nodes", "compute_sets"):
        for row in session.execute(
            f"SELECT uuid, interfaces FROM {table} WHERE interfaces IS NOT NULL", None
        ).fetchall():
            interfaces = row["interfaces"]
            has_groups = any(
                (item.get("config") or {}).get(key)
                for item in interfaces
                for key in ("security_groups", "identity_groups")
            )
            if table == "compute_sets" and has_groups:
                session.execute(
                    "UPDATE compute_sets SET interfaces = NULL, networking = %s::jsonb[], updated_at = NOW() WHERE uuid = %s",
                    (
                        [json.dumps(item) for item in networking_template(interfaces)],
                        str(row["uuid"]),
                    ),
                )
                continue
            if has_groups:
                require_group_bindings(
                    session,
                    "em_core_compute_nodes",
                    row["uuid"],
                    {"interfaces": interfaces},
                )
            session.execute(
                f"UPDATE {table} SET interfaces = %s::jsonb[], updated_at = NOW() WHERE uuid = %s",
                (
                    [json.dumps(canonical_interface(item)) for item in interfaces],
                    str(row["uuid"]),
                ),
            )


def canonical_resource_value(kind, value):
    result = dict(value)
    if kind == "em_core_network_ports":
        return canonical_interface(value)
    elif result.get("interfaces") is not None:
        interfaces = result["interfaces"]
        if not isinstance(interfaces, list) or any(
            not isinstance(item, dict) for item in interfaces
        ):
            raise ValueError("expand referenced interfaces before upgrading")
        has_groups = any(
            (item.get("config") or {}).get(key)
            for item in interfaces
            for key in ("security_groups", "identity_groups")
        )
        if kind == "em_core_compute_sets" and has_groups:
            result["networking"] = networking_template(interfaces)
            result["interfaces"] = None
        else:
            result["interfaces"] = [canonical_interface(item) for item in interfaces]
    return result


def require_group_bindings(session, kind, identifier, value):
    for position, item in enumerate(value.get("interfaces") or [value]):
        index = item.get("index") if item.get("index") is not None else position
        for suffix, field in (
            ("security", "security_group"),
            ("identity", "identity_group"),
        ):
            for group in (item.get("config") or {}).get(suffix + "_groups", []):
                try:
                    group = uuid.UUID(str(group))
                except (ValueError, TypeError, AttributeError) as exc:
                    raise ValueError(
                        "expand referenced group lists before upgrading"
                    ) from exc
                if kind == "em_core_network_ports":
                    query = f"SELECT 1 FROM net_port_{suffix}_groups WHERE port = %s AND {field} = %s"
                    params = (str(identifier), str(group))
                else:
                    query = f"SELECT 1 FROM net_port_{suffix}_groups g JOIN compute_ports p ON p.uuid = g.port WHERE p.node = %s AND p.index = %s AND g.{field} = %s"
                    params = (str(identifier), index, str(group))
                if session.execute(query, params).fetchone() is None:
                    raise ValueError(
                        f"finish provisioning resource {identifier} group bindings before upgrading"
                    )


def migrate_declarations(session):
    from gcl_sdk.agents.universal import utils as sdk_utils

    suffixes = {
        ".compute.nodes": "em_core_compute_nodes",
        ".compute.sets": "em_core_compute_sets",
        ".network.ports": "em_core_network_ports",
    }
    for row in session.execute(
        "SELECT uuid, resource_link_prefix, value FROM em_resources",
        None,
    ).fetchall():
        kind = next(
            (
                kind
                for suffix, kind in suffixes.items()
                if row["resource_link_prefix"].endswith(suffix)
            ),
            None,
        )
        if kind is None:
            continue
        value = row["value"]
        normalized = canonical_resource_value(kind, value)
        if normalized == value:
            continue
        if kind != "em_core_compute_sets":
            require_group_bindings(session, kind, row["uuid"], value)
        session.execute(
            "UPDATE em_resources SET value = %s::jsonb, updated_at = NOW() WHERE uuid = %s",
            (json.dumps(normalized), str(row["uuid"])),
        )
    kinds = tuple(suffixes.values()) + ("set_agent_node", "node")
    for table in ("ua_target_resources", "ua_actual_resources"):
        for row in session.execute(
            f"SELECT uuid, kind, value FROM {table} WHERE kind = ANY(%s)",
            (list(kinds),),
        ).fetchall():
            kind = (
                "em_core_compute_nodes"
                if row["kind"] in ("set_agent_node", "node")
                else row["kind"]
            )
            if kind != "em_core_compute_sets":
                require_group_bindings(session, kind, row["uuid"], row["value"])
            value = canonical_resource_value(kind, row["value"])
            if value == row["value"]:
                continue
            if table == "ua_actual_resources":
                # The owner refreshes the target hash from the canonical model.
                session.execute(
                    "UPDATE ua_actual_resources SET value = %s::jsonb, hash = '', full_hash = %s, updated_at = NOW() WHERE uuid = %s AND kind = %s",
                    (
                        json.dumps(value),
                        sdk_utils.calculate_hash(value),
                        str(row["uuid"]),
                        row["kind"],
                    ),
                )
            else:
                persistent = dict(value)
                if kind == "em_core_compute_nodes":
                    persistent.pop("networking", None)
                    persistent.pop("network_activation_barrier", None)
                elif kind == "em_core_network_ports":
                    persistent.pop("activation_barrier", None)
                session.execute(
                    "UPDATE ua_target_resources SET value = %s::jsonb, hash = %s, updated_at = NOW(), tracked_at = NOW(), status = 'IN_PROGRESS' WHERE uuid = %s AND kind = %s",
                    (
                        json.dumps(value),
                        sdk_utils.calculate_hash(persistent),
                        str(row["uuid"]),
                        row["kind"],
                    ),
                )


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0007-network-sdn-9e41c7.py"]

    @property
    def migration_id(self):
        return "15df7a28-b649-4860-9dfa-8d98b2c07eac"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        self._upgrade_contract(session)
        session.execute(
            "CREATE UNIQUE INDEX net_addresses_floating_reserved_idx "
            "ON net_addresses(address) WHERE kind = 'floating' "
            "AND allocation = 'reserved'",
            None,
        )
        session.execute(
            "ALTER TABLE repo_repositories ADD COLUMN "
            "refresh_requested boolean NOT NULL DEFAULT false",
            None,
        )
        self._canonicalize_config(session)

    def downgrade(self, session):
        saved = session.execute(
            "SELECT 1 FROM compute_ports "
            "UNION ALL SELECT 1 FROM nodes WHERE interfaces IS NOT NULL "
            "UNION ALL SELECT 1 FROM compute_sets WHERE interfaces IS NOT NULL OR networking IS NOT NULL "
            "UNION ALL SELECT 1 FROM ua_target_resources WHERE kind IN ('em_core_compute_nodes', 'em_core_compute_sets', 'em_core_network_ports', 'set_agent_node') "
            "UNION ALL SELECT 1 FROM em_resources WHERE resource_link_prefix LIKE '%%.compute.nodes' OR resource_link_prefix LIKE '%%.compute.sets' OR resource_link_prefix LIKE '%%.network.ports' LIMIT 1",
            None,
        ).fetchone()
        if saved is not None:
            raise ValueError(
                "restore the database backup to revert canonical declarations"
            )
        session.execute(
            "ALTER TABLE repo_repositories DROP COLUMN refresh_requested", None
        )
        session.execute("DROP INDEX net_addresses_floating_reserved_idx", None)
        self._downgrade_contract(session)

    def _canonicalize_config(self, session):
        pending_rule = session.execute(
            "SELECT r.uuid FROM em_resources r "
            "WHERE r.resource_link_prefix LIKE '%%.network.catalog.security_group_rules' "
            "AND NOT EXISTS (SELECT 1 FROM net_security_group_rules s "
            "WHERE s.uuid = CASE WHEN r.value ? 'uuid' THEN (r.value->>'uuid')::uuid ELSE r.uuid END) LIMIT 1",
            None,
        ).fetchone()
        if pending_rule is not None:
            raise ValueError(
                f"finish provisioning manifest security group rules before upgrading: {pending_rule['uuid']}"
            )
        session.execute(
            "ALTER TABLE compute_sets ADD COLUMN IF NOT EXISTS network_activation_barrier JSONB NOT NULL DEFAULT '{}'",
            None,
        )
        session.execute(
            "UPDATE compute_sets s SET network_activation_barrier = t.value->'network_activation_barrier' "
            "FROM ua_target_resources t WHERE t.uuid = s.uuid AND t.kind = 'em_core_compute_sets' "
            "AND jsonb_typeof(t.value->'network_activation_barrier') = 'object'",
            None,
        )
        session.execute("DROP TABLE IF EXISTS sdn_creation_receipts", None)
        session.execute(
            "UPDATE compute_ports SET "
            "dhcp = COALESCE(dhcp, CASE WHEN config->'dhcp' = 'false'::jsonb THEN FALSE END), "
            "dns = COALESCE(dns, CASE WHEN config->'dns' = 'false'::jsonb THEN FALSE END), "
            "network_identity = COALESCE(network_identity, CASE WHEN config->'subnet_group' = 'false'::jsonb THEN FALSE END)",
            None,
        )
        for suffix, field in (
            ("security", "security_group"),
            ("identity", "identity_group"),
        ):
            session.execute(
                f"""
                INSERT INTO net_port_{suffix}_groups
                    (uuid, project_id, created_at, updated_at, port, {field})
                SELECT md5(p.uuid::text || ':{suffix}:' || g.id)::uuid,
                       p.project_id, p.created_at, p.updated_at, p.uuid, g.id::uuid
                FROM compute_ports p,
                     jsonb_array_elements_text(COALESCE(p.config->'{suffix}_groups', '[]'::jsonb)) AS g(id)
                ON CONFLICT (port, {field}) DO NOTHING
                """,
                None,
            )
        session.execute(
            "UPDATE compute_ports SET config = config - 'dhcp' - 'dns' - 'subnet_group' - 'security_groups' - 'identity_groups', updated_at = NOW()",
            None,
        )
        migrate_interfaces(session)
        migrate_declarations(session)

    def _upgrade_contract(self, session):
        for name in PERMISSIONS + PRIVILEGED_PERMISSIONS:
            identifier = permission_uuid(name)
            session.execute(
                "INSERT INTO iam_permissions (uuid, name, description) VALUES (%s, %s, %s) ON CONFLICT (uuid) DO NOTHING",
                (identifier, name, name),
            )
            if name in PRIVILEGED_PERMISSIONS:
                continue
            session.execute(
                "INSERT INTO iam_binding_permissions (uuid, role, permission, project_id) VALUES (%s, '726f6c65-0000-0000-0000-000000000002', %s, '00000000-0000-0000-0000-000000000000') ON CONFLICT (uuid) DO NOTHING",
                (permission_uuid("binding." + name), identifier),
            )

        for expression in (
            "ALTER TABLE compute_ports ADD COLUMN activation_barrier JSONB NOT NULL DEFAULT '{}'",
            "ALTER TABLE net_identity_groups ADD COLUMN automatic BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE net_nfs ADD COLUMN generated_config JSONB NOT NULL DEFAULT '{}'",
            "UPDATE net_nfs SET generated_config = config, config = '{}'::jsonb WHERE provenance = 'generated'",
            "ALTER TABLE compute_networks ADD COLUMN driver_state JSONB NOT NULL DEFAULT '{}'",
            "UPDATE compute_networks SET driver_state = driver_spec - 'driver' WHERE driver_spec->>'driver' = 'ovs_evpn'",
            "UPDATE compute_networks SET driver_spec = jsonb_build_object('driver', 'ovs_evpn') || CASE WHEN driver_spec ? 'mtu' THEN jsonb_build_object('mtu', driver_spec->'mtu') ELSE '{}'::jsonb END WHERE driver_spec->>'driver' = 'ovs_evpn'",
            "ALTER TABLE nodes ADD COLUMN network_initialized BOOLEAN NOT NULL DEFAULT FALSE",
            "UPDATE nodes SET network_initialized = TRUE WHERE EXISTS (SELECT 1 FROM compute_ports p WHERE p.node = nodes.uuid)",
            "ALTER TABLE compute_sets ADD COLUMN networking JSONB[]",
            "ALTER TABLE compute_sets ADD COLUMN network_activation_barrier JSONB NOT NULL DEFAULT '{}'",
            "ALTER TABLE compute_subnets ADD COLUMN floating_pool BOOLEAN NOT NULL DEFAULT FALSE",
            "UPDATE compute_subnets s SET floating_pool = TRUE FROM compute_networks n WHERE s.network = n.uuid AND s.placeable = FALSE AND (n.driver_spec->>'driver' IS DISTINCT FROM 'ovs_evpn' OR n.access = 'public')",
            "ALTER TABLE compute_ports ALTER COLUMN index DROP NOT NULL, ALTER COLUMN index DROP DEFAULT",
            "UPDATE compute_ports SET index = NULL WHERE node IS NULL",
            "ALTER TABLE compute_ports ADD COLUMN target_mac VARCHAR(17), ADD COLUMN dhcp BOOLEAN, ADD COLUMN dns BOOLEAN, ADD COLUMN network_identity BOOLEAN",
            "UPDATE compute_ports SET dhcp = CASE WHEN config->'dhcp' = 'false'::jsonb THEN FALSE ELSE NULL END, dns = CASE WHEN config->'dns' = 'false'::jsonb THEN FALSE ELSE NULL END, network_identity = CASE WHEN config->'subnet_group' = 'false'::jsonb THEN FALSE ELSE NULL END",
            "UPDATE compute_ports SET config = config - 'dhcp' - 'dns' - 'subnet_group'",
            "ALTER TABLE net_addresses ADD COLUMN kind VARCHAR(16) NOT NULL DEFAULT 'reservation', ADD COLUMN port UUID REFERENCES compute_ports(uuid), ADD COLUMN delete_requested BOOLEAN NOT NULL DEFAULT FALSE",
            "UPDATE net_addresses SET kind = 'floating', port = association WHERE origin = 'floating' OR association IS NOT NULL",
            "CREATE UNIQUE INDEX ON net_addresses(port) WHERE port IS NOT NULL",
            """
            CREATE TABLE net_security_group_rules (
                uuid UUID PRIMARY KEY,
                project_id UUID NOT NULL,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                security_group UUID NOT NULL REFERENCES net_security_groups(uuid),
                direction VARCHAR(16) NOT NULL,
                protocol VARCHAR(16) NOT NULL DEFAULT 'any',
                port INTEGER,
                remote_ip VARCHAR(43),
                remote_group UUID REFERENCES net_identity_groups(uuid),
                CHECK (direction IN ('ingress', 'egress')),
                CHECK (protocol IN ('any', 'tcp', 'udp', 'icmp')),
                CHECK (port IS NULL OR (port BETWEEN 1 AND 65535 AND protocol IN ('tcp', 'udp'))),
                CHECK (remote_ip IS NULL OR remote_group IS NULL),
                CHECK (remote_group IS NULL OR direction = 'ingress')
            )
            """,
            "CREATE INDEX ON net_security_group_rules(project_id, security_group)",
            """
            INSERT INTO net_security_group_rules
                (uuid, project_id, created_at, updated_at, security_group,
                 direction, protocol, port, remote_ip, remote_group)
            SELECT md5(s.uuid::text || ':rule:' || r.ordinality::text)::uuid,
                   s.project_id, s.created_at, s.updated_at, s.uuid,
                   r.rule->>'direction', COALESCE(r.rule->>'protocol', 'any'),
                   (r.rule->>'port')::integer, r.rule->>'remote_ip',
                   (r.rule->>'remote_group')::uuid
            FROM net_security_groups s,
                 unnest(s.rules) WITH ORDINALITY AS r(rule, ordinality)
            """,
            "ALTER TABLE net_security_groups DROP COLUMN rules",
        ):
            session.execute(expression, None)
        for suffix, group_table, group_field in (
            ("security", "net_security_groups", "security_group"),
            ("identity", "net_identity_groups", "identity_group"),
        ):
            session.execute(
                f"""
                CREATE TABLE net_port_{suffix}_groups (
                    uuid UUID PRIMARY KEY,
                    project_id UUID NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP NOT NULL,
                    port UUID NOT NULL REFERENCES compute_ports(uuid),
                    {group_field} UUID NOT NULL REFERENCES {group_table}(uuid),
                    UNIQUE (port, {group_field})
                )
            """,
                None,
            )
            session.execute(
                f"CREATE INDEX ON net_port_{suffix}_groups(project_id)", None
            )
            session.execute(
                f"""
                INSERT INTO net_port_{suffix}_groups
                    (uuid, project_id, created_at, updated_at, port, {group_field})
                SELECT md5(p.uuid::text || ':{suffix}:' || g.id)::uuid,
                       p.project_id, p.created_at, p.updated_at, p.uuid, g.id::uuid
                FROM compute_ports p,
                     jsonb_array_elements_text(COALESCE(p.config->'{suffix}_groups', '[]'::jsonb)) AS g(id)
                ON CONFLICT (port, {group_field}) DO NOTHING
            """,
                None,
            )
            session.execute(
                f"UPDATE compute_ports SET config = config - '{suffix}_groups'", None
            )

        migrate_interfaces(session)
        migrate_declarations(session)

        # Existing overlay networks get the same protected network-wide identity
        # as new ones. Flat networks remain untouched.
        namespace = uuid.UUID("6e66736c-0000-0000-0000-000000000001")
        for network in session.execute(
            "SELECT uuid, project_id FROM compute_networks WHERE driver_spec->>'driver' = 'ovs_evpn'",
            None,
        ).fetchall():
            group_uuid = uuid.uuid5(namespace, "identity:%s" % network["uuid"])
            groups = session.execute(
                "SELECT uuid, identity FROM net_identity_groups WHERE network = %s",
                (str(network["uuid"]),),
            ).fetchall()
            if any(str(group["uuid"]) == str(group_uuid) for group in groups):
                session.execute(
                    "UPDATE net_identity_groups SET automatic = TRUE WHERE uuid = %s",
                    (str(group_uuid),),
                )
                continue
            taken = {group["identity"] for group in groups}
            bit = next(
                (1 << index for index in range(16) if 1 << index not in taken), None
            )
            if bit is None:
                raise ValueError(
                    "no identity bit available for network %s" % network["uuid"]
                )
            session.execute(
                "INSERT INTO net_identity_groups (uuid, name, description, created_at, updated_at, project_id, network, identity, automatic) VALUES (%s, 'network-identity', '', NOW(), NOW(), %s, %s, %s, TRUE)",
                (
                    str(group_uuid),
                    str(network["project_id"]),
                    str(network["uuid"]),
                    bit,
                ),
            )

    def _downgrade_contract(self, session):
        managed = session.execute(
            "SELECT 1 FROM ua_target_resources WHERE kind IN ('em_core_compute_nodes', 'em_core_compute_sets', 'em_core_network_ports', 'set_agent_node') UNION ALL SELECT 1 FROM em_resources WHERE resource_link_prefix LIKE '%%.compute.nodes' OR resource_link_prefix LIKE '%%.compute.sets' OR resource_link_prefix LIKE '%%.network.ports' LIMIT 1",
            None,
        ).fetchone()
        if managed is not None:
            raise ValueError(
                "downgrade of managed SDN declarations requires restoring a database backup"
            )
        forced_dhcp = session.execute(
            "SELECT 1 FROM compute_ports p JOIN compute_subnets s ON s.uuid = p.subnet WHERE p.dhcp = TRUE AND s.dhcp = FALSE LIMIT 1",
            None,
        ).fetchone()
        if forced_dhcp is not None:
            raise ValueError(
                "explicit DHCP enablement cannot be represented by legacy Port config"
            )
        for name in PERMISSIONS + PRIVILEGED_PERMISSIONS:
            session.execute(
                "DELETE FROM iam_binding_permissions WHERE permission = %s",
                (permission_uuid(name),),
            )
            session.execute(
                "DELETE FROM iam_permissions WHERE uuid = %s", (permission_uuid(name),)
            )

        session.execute(
            "ALTER TABLE compute_ports DROP COLUMN activation_barrier", None
        )
        session.execute("ALTER TABLE net_identity_groups DROP COLUMN automatic", None)
        session.execute("UPDATE net_nfs SET config = generated_config || config", None)
        session.execute("ALTER TABLE net_nfs DROP COLUMN generated_config", None)
        session.execute(
            "UPDATE compute_networks SET driver_spec = driver_spec || driver_state WHERE driver_spec->>'driver' = 'ovs_evpn'",
            None,
        )
        session.execute("ALTER TABLE compute_networks DROP COLUMN driver_state", None)
        # Reconstruct the legacy representations before removing canonical rows.
        session.execute(
            "ALTER TABLE net_security_groups ADD COLUMN rules JSONB[] NOT NULL DEFAULT '{}'",
            None,
        )
        session.execute(
            """
            UPDATE net_security_groups s SET rules = (
                SELECT COALESCE(array_agg(jsonb_strip_nulls(jsonb_build_object(
                    'direction', r.direction, 'protocol', r.protocol, 'port', r.port,
                    'remote_ip', r.remote_ip, 'remote_group', r.remote_group
                )) ORDER BY r.uuid), '{}'::jsonb[])
                FROM net_security_group_rules r WHERE r.security_group = s.uuid
            )
        """,
            None,
        )
        for suffix, group_field in (
            ("security", "security_group"),
            ("identity", "identity_group"),
        ):
            session.execute(
                f"""
                UPDATE compute_ports p SET config = p.config || jsonb_build_object(
                    '{suffix}_groups', COALESCE((SELECT jsonb_agg(g.{group_field} ORDER BY g.uuid)
                    FROM net_port_{suffix}_groups g WHERE g.port = p.uuid), '[]'::jsonb)
                )
            """,
                None,
            )
            session.execute(f"DROP TABLE net_port_{suffix}_groups", None)
        session.execute("DROP TABLE net_security_group_rules", None)
        session.execute(
            "UPDATE compute_ports SET config = config || jsonb_strip_nulls(jsonb_build_object('dhcp', dhcp, 'dns', dns, 'subnet_group', network_identity))",
            None,
        )
        for row in session.execute(
            "SELECT uuid, networking FROM compute_sets WHERE interfaces IS NULL AND networking IS NOT NULL",
            None,
        ).fetchall():
            interfaces = []
            for entry in row["networking"]:
                port = entry["port"]
                floating = entry.get("floating_ip") or {}
                if (
                    not isinstance(port, dict)
                    or not isinstance(floating, dict)
                    or set(port)
                    - {
                        "subnet",
                        "network",
                        "dhcp",
                        "dns",
                        "network_identity",
                        "port_security",
                    }
                    or set(floating) - {"subnet"}
                    or port.get("dhcp") is True
                ):
                    raise ValueError(
                        f"convert NodeSet {row['uuid']} networking to legacy interfaces before downgrading"
                    )
                config = {"kind": "simple"}
                for key in ("security_groups", "identity_groups"):
                    if entry.get(key):
                        config[key] = entry[key]
                if floating:
                    config["public"] = {"floating_from": floating["subnet"]}
                interface = {"kind": "subnet", "config": config}
                for key in (
                    "subnet",
                    "network",
                    "dhcp",
                    "dns",
                    "network_identity",
                    "port_security",
                ):
                    if key in port:
                        interface[key] = port[key]
                if "index" in entry:
                    interface["index"] = entry["index"]
                interfaces.append(json.dumps(interface))
            session.execute(
                "UPDATE compute_sets SET interfaces = %s::jsonb[] WHERE uuid = %s",
                (interfaces, str(row["uuid"])),
            )
        for table in ("nodes", "compute_sets"):
            for row in session.execute(
                f"SELECT uuid, interfaces FROM {table} WHERE interfaces IS NOT NULL",
                None,
            ).fetchall():
                interfaces = []
                for position, interface in enumerate(row["interfaces"]):
                    value = dict(interface)
                    if value.get("dhcp") is True:
                        raise ValueError(
                            "explicit DHCP enablement cannot be represented by legacy interface config"
                        )
                    config = dict(value.get("config") or {"kind": "simple"})
                    for new, old in (
                        ("dhcp", "dhcp"),
                        ("dns", "dns"),
                        ("network_identity", "subnet_group"),
                    ):
                        if new in value:
                            config[old] = value.pop(new)
                    if value.get("kind") == "subnet":
                        if table == "nodes":
                            index = (
                                value.get("index")
                                if value.get("index") is not None
                                else position
                            )
                            port = session.execute(
                                "SELECT config FROM compute_ports WHERE node = %s AND index = %s",
                                (str(row["uuid"]), index),
                            ).fetchone()
                            if port is not None:
                                for key in ("security_groups", "identity_groups"):
                                    config[key] = port["config"].get(key, [])
                        value["config"] = config
                    interfaces.append(json.dumps(value))
                session.execute(
                    f"UPDATE {table} SET interfaces = %s::jsonb[] WHERE uuid = %s",
                    (interfaces, str(row["uuid"])),
                )
        for expression in (
            "ALTER TABLE compute_sets DROP COLUMN networking, DROP COLUMN network_activation_barrier",
            "ALTER TABLE nodes DROP COLUMN network_initialized",
            "ALTER TABLE net_addresses DROP COLUMN delete_requested, DROP COLUMN port, DROP COLUMN kind",
            "ALTER TABLE compute_ports DROP COLUMN target_mac, DROP COLUMN dhcp, DROP COLUMN dns, DROP COLUMN network_identity",
            "UPDATE compute_ports SET index = 0 WHERE index IS NULL",
            "ALTER TABLE compute_ports ALTER COLUMN index SET NOT NULL, ALTER COLUMN index SET DEFAULT 0",
            "ALTER TABLE compute_subnets DROP COLUMN floating_pool",
        ):
            session.execute(expression, None)


migration_step = MigrationStep()
