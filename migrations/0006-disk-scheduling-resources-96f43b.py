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

import copy
import json

from gcl_sdk.infra import constants as ic
from restalchemy.storage.sql import migrations

RESOURCE_KINDS = (
    "node",
    "machine",
    "volume",
    "pool_machine",
    "pool_volume",
    "pool",
    "guest_machine",
    "set_agent_node",
    "target_node_set",
    "set_agent_volume",
    "em_core_compute_nodes",
    "em_core_compute_sets",
    "em_core_compute_volumes",
    "em_core_compute_hypervisors",
)


def enrich(value, volume=False, mask=False, pool_volume=False):
    """Fill missing disk scheduling fields without changing existing policy."""
    result = copy.deepcopy(value)
    defaults = {"tier": ic.DiskTier.WARM.value, "ephemeral": True}
    if mask:
        defaults = dict.fromkeys(defaults)
    if volume:
        for key, default in defaults.items():
            result.setdefault(key, default)
    if pool_volume:
        result.setdefault("storage_pool", None)
    spec = result.get("disk_spec")
    if isinstance(spec, dict):
        if spec.get("kind") == "root_disk" or (mask and "size" in spec):
            for key, default in defaults.items():
                spec.setdefault(key, default)
        elif isinstance(spec.get("disks"), list):
            for disk in spec["disks"]:
                if not isinstance(disk, dict):
                    continue
                for key, default in defaults.items():
                    disk.setdefault(key, default)
    pools = result.get("storage_pools")
    if isinstance(pools, list):
        for pool in pools:
            if not isinstance(pool, dict):
                continue
            for key, default in defaults.items():
                pool.setdefault(key, default)
    driver = result.get("driver_spec")
    converted = False
    if isinstance(driver, dict) and driver.get("kind") == "exordos_local_hyper":
        name = driver.get("storage_pool")
        if isinstance(name, str):
            driver["storage_pool"] = [{"name": name, **defaults}] if name else []
            converted = True
    fields = result.get("target_fields")
    if isinstance(fields, dict):
        result["target_fields"] = enrich(
            fields, volume=volume, mask=True, pool_volume=pool_volume
        )
    if converted and isinstance(fields, dict):
        driver_fields = result["target_fields"].get("driver_spec")
        if isinstance(driver_fields, dict):
            driver_fields["storage_pool"] = [
                dict.fromkeys(entry) for entry in driver["storage_pool"]
            ]
    return result


COMPUTE_PREFIXES = {
    "$core.compute.nodes",
    "$core.compute.sets",
    "$core.compute.volumes",
    "$core.compute.hypervisors",
}


def enrich_resources(resources):
    result = copy.deepcopy(resources)
    for prefix, instances in result.items():
        if prefix not in COMPUTE_PREFIXES or not isinstance(instances, dict):
            continue
        for name, value in instances.items():
            if isinstance(value, dict):
                instances[name] = enrich(
                    value, volume=prefix == "$core.compute.volumes"
                )
    return result


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = [
            "0005-volumes-tier-ephemeral-fba1de.py",
        ]

    @property
    def migration_id(self):
        return "96f43b10-189d-441d-bf21-daa693aa601d"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        # Enrich the persisted sources too, so builders keep the same defaults.
        for table, column in (
            ("nodes", "disk_spec"),
            ("compute_sets", "disk_spec"),
            ("machine_pools", "storage_pools"),
            ("machine_pools", "driver_spec"),
        ):
            rows = session.execute(f"SELECT uuid, {column} FROM {table}").fetchall()
            for row in rows:
                old = row[column]
                new = enrich({column: old})[column]
                if new != old:
                    expression = (
                        "ARRAY(SELECT jsonb_array_elements(CAST(%s AS jsonb)))"
                        if column == "storage_pools"
                        else "CAST(%s AS jsonb)"
                    )
                    session.execute(
                        f"UPDATE {table} SET {column} = {expression}, "
                        "updated_at = current_timestamp WHERE uuid = %s",
                        (json.dumps(new), row["uuid"]),
                    )
        # EM rebuilds target values from these sources after hashes are reset.
        rows = session.execute(
            "SELECT uuid, resource_link_prefix, value FROM em_resources"
        ).fetchall()
        for row in rows:
            if row["resource_link_prefix"] not in COMPUTE_PREFIXES:
                continue
            value = enrich(
                row["value"],
                volume=row["resource_link_prefix"] == "$core.compute.volumes",
            )
            session.execute(
                "UPDATE em_resources SET value = CAST(%s AS jsonb), full_hash = '', "
                "updated_at = current_timestamp WHERE uuid = %s",
                (json.dumps(value), row["uuid"]),
            )
        # A manifest upgrade can regenerate EM resources, so update the stored
        # manifests too, preserving explicit policies and template references.
        for table, column in (
            ("em_manifests", "resources"),
            ("repo_elements", "manifest"),
        ):
            rows = session.execute(f"SELECT uuid, {column} FROM {table}").fetchall()
            for row in rows:
                old = row[column]
                if not isinstance(old, dict):
                    continue
                if column == "resources":
                    new = enrich_resources(old)
                else:
                    new = copy.deepcopy(old)
                    if isinstance(new.get("resources"), dict):
                        new["resources"] = enrich_resources(new["resources"])
                if new != old:
                    session.execute(
                        f"UPDATE {table} SET {column} = CAST(%s AS jsonb), updated_at = current_timestamp WHERE uuid = %s",
                        (json.dumps(new), row["uuid"]),
                    )
        # Touch owners so their builders regenerate the invalidated target hashes.
        for table in (
            "nodes",
            "compute_sets",
            "machine_pools",
            "machines",
            "node_volumes",
            "compute_machine_volumes",
        ):
            session.execute(f"UPDATE {table} SET updated_at = current_timestamp")
        for table in ("ua_target_resources", "ua_actual_resources"):
            rows = session.execute(
                f"SELECT res_uuid, kind, value FROM {table} WHERE kind = ANY(%s)",
                (list(RESOURCE_KINDS),),
            ).fetchall()
            for row in rows:
                value = enrich(
                    row["value"],
                    volume=row["kind"]
                    in (
                        "volume",
                        "pool_volume",
                        "set_agent_volume",
                        "em_core_compute_volumes",
                    ),
                    pool_volume=row["kind"] == "pool_volume",
                )
                # Rebuild target hashes from the enriched fields; actual hashes
                # are refreshed from the agent's next report, never invented here.
                extra = (
                    ", master_hash = '', master_full_hash = ''"
                    if table == "ua_target_resources"
                    else ""
                )
                session.execute(
                    f"UPDATE {table} SET value = CAST(%s AS jsonb), "
                    "hash = '', full_hash = '', updated_at = current_timestamp"
                    + extra
                    + " WHERE res_uuid = %s",
                    (json.dumps(value), row["res_uuid"]),
                )

    def downgrade(self, session):
        # Backfilled JSON fields remain valid optional data for the old models.
        pass


migration_step = MigrationStep()
