# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0

import json

from restalchemy.storage.sql import migrations


def default_network_template(entries, *, template=False):
    from exordos_core.network.node_creation import validate_default_network

    if entries == []:
        return {}
    if not isinstance(entries, list) or len(entries) != 1:
        raise ValueError(
            "convert multi-port networking templates to independent Port resources before upgrading"
        )
    entry = entries[0]
    if (
        not isinstance(entry, dict)
        or set(entry) - {"port", "index", "floating_ip"}
        or entry.get("index", 0) != 0
    ):
        raise ValueError(
            "convert networking groups and non-primary ports to independent resources before upgrading"
        )
    if not isinstance(entry.get("port"), dict):
        raise ValueError(
            "convert adopted networking ports to independent Port resources before upgrading"
        )
    value = dict(entry["port"])
    if "floating_ip" in entry:
        value["floating_ip"] = entry["floating_ip"]
    if not value:
        return None
    validate_default_network(value, template=template)
    return value


def migrate_networking(session):
    from gcl_sdk.agents.universal import utils as sdk_utils

    for row in session.execute(
        "SELECT uuid, networking FROM compute_sets WHERE networking IS NOT NULL", None
    ).fetchall():
        value = default_network_template(row["networking"], template=True)
        session.execute(
            "UPDATE compute_sets SET default_network = %s::jsonb, updated_at = NOW() WHERE uuid = %s",
            (json.dumps(value) if value is not None else None, str(row["uuid"])),
        )
    for table in ("em_resources", "ua_target_resources", "ua_actual_resources"):
        kind_column = "resource_link_prefix" if table == "em_resources" else "kind"
        for row in session.execute(
            f"SELECT uuid, {kind_column}, value FROM {table}", None
        ).fetchall():
            kind = row[kind_column]
            is_set = kind == "em_core_compute_sets" or kind.endswith(".compute.sets")
            is_node = kind in (
                "em_core_compute_nodes",
                "set_agent_node",
                "node",
            ) or kind.endswith(".compute.nodes")
            if not (is_set or is_node):
                continue
            value = dict(row["value"])
            changed = "network_activation_barrier" in value
            value.pop("network_activation_barrier", None)
            if "networking" in value:
                entries = value.pop("networking")
                changed = True
                if entries is not None and table != "ua_actual_resources":
                    if value.get("default_network"):
                        raise ValueError(
                            f"conflicting networking/default_network in {table} resource {row['uuid']}"
                        )
                    request = default_network_template(entries, template=is_set)
                    if request is None:
                        value.pop("default_network", None)
                    else:
                        value["default_network"] = request
            if not changed:
                continue
            if table == "em_resources":
                session.execute(
                    "UPDATE em_resources SET value = %s::jsonb, updated_at = NOW() WHERE uuid = %s",
                    (json.dumps(value), str(row["uuid"])),
                )
            elif table == "ua_actual_resources":
                session.execute(
                    "UPDATE ua_actual_resources SET value = %s::jsonb, hash = '', full_hash = %s, updated_at = NOW() WHERE uuid = %s AND kind = %s",
                    (
                        json.dumps(value),
                        sdk_utils.calculate_hash(value),
                        str(row["uuid"]),
                        kind,
                    ),
                )
            else:
                persistent = dict(value)
                if is_node:
                    persistent.pop("default_network", None)
                session.execute(
                    "UPDATE ua_target_resources SET value = %s::jsonb, hash = %s, updated_at = NOW(), tracked_at = NOW(), status = 'IN_PROGRESS' WHERE uuid = %s AND kind = %s",
                    (
                        json.dumps(value),
                        sdk_utils.calculate_hash(persistent),
                        str(row["uuid"]),
                        kind,
                    ),
                )


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0009-flat-port-source-89f12ab4.py"]

    @property
    def migration_id(self):
        return "a182bc39-b37f-46ae-9b18-cf831ebae12a"

    def upgrade(self, session):
        session.execute(
            "ALTER TABLE compute_ports ADD COLUMN legacy_flat_placement BOOLEAN NOT NULL DEFAULT FALSE"
        )
        session.execute(
            "ALTER TABLE compute_networks ADD COLUMN is_default BOOLEAN NOT NULL DEFAULT FALSE"
        )
        session.execute(
            "CREATE UNIQUE INDEX compute_networks_default_project_idx "
            "ON compute_networks(project_id) WHERE is_default"
        )
        session.execute(
            "ALTER TABLE compute_subnets ADD COLUMN default_floating_pool BOOLEAN NOT NULL DEFAULT FALSE"
        )
        session.execute(
            "ALTER TABLE compute_subnets ADD CONSTRAINT default_floating_pool_valid "
            "CHECK (NOT default_floating_pool OR (floating_pool AND NOT placeable))"
        )
        session.execute(
            "CREATE UNIQUE INDEX compute_subnets_default_floating_project_idx "
            "ON compute_subnets(project_id) WHERE default_floating_pool"
        )
        # Old empty templates meant omission; preserve that on upgrade.
        session.execute(
            "ALTER TABLE compute_sets ALTER COLUMN default_network DROP NOT NULL"
        )
        session.execute(
            "UPDATE compute_sets SET default_network = NULL WHERE default_network = '{}'::jsonb"
        )
        migrate_networking(session)
        session.execute(
            "ALTER TABLE compute_sets DROP COLUMN networking, DROP COLUMN network_activation_barrier"
        )

    def downgrade(self, session):
        session.execute(
            "ALTER TABLE compute_sets ADD COLUMN networking JSONB[], ADD COLUMN network_activation_barrier JSONB NOT NULL DEFAULT '{}'::jsonb"
        )
        session.execute("ALTER TABLE compute_ports DROP COLUMN legacy_flat_placement")
        session.execute(
            "UPDATE compute_sets SET default_network = '{}'::jsonb WHERE default_network IS NULL"
        )
        session.execute(
            "ALTER TABLE compute_sets ALTER COLUMN default_network SET NOT NULL"
        )
        session.execute("ALTER TABLE compute_subnets DROP COLUMN default_floating_pool")
        session.execute("ALTER TABLE compute_networks DROP COLUMN is_default")


migration_step = MigrationStep()
