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

import typing as tp

from gcl_sdk.agents.universal.drivers import pool as ua_pool

from exordos_core.compute import constants as nc
from exordos_core.compute.dm import models as compute_models
from exordos_core.elements.dm import models as em_models
from exordos_core.metrics.collectors import base

# Numeric codes of the node and set statuses. Zero is deliberately unused so
# that a missing value is never mistaken for a status.
NODE_STATUS_CODES = {
    nc.NodeStatus.NEW.value: 1,
    nc.NodeStatus.SCHEDULED.value: 2,
    nc.NodeStatus.IN_PROGRESS.value: 3,
    nc.NodeStatus.STARTED.value: 4,
    nc.NodeStatus.ACTIVE.value: 5,
    nc.NodeStatus.ERROR.value: 6,
}

POOL_STATUS_CODES = {
    ua_pool.MachinePoolStatus.ACTIVE.value: 1,
    ua_pool.MachinePoolStatus.IN_PROGRESS.value: 2,
    ua_pool.MachinePoolStatus.MAINTENANCE.value: 3,
    ua_pool.MachinePoolStatus.DISABLED.value: 4,
}

NODE_INFO = "exordos_compute_node_info"
NODE_STATUS = "exordos_compute_node_status"
NODE_CORES = "exordos_compute_node_cores"
NODE_RAM = "exordos_compute_node_ram_bytes"
NODE_DISK = "exordos_compute_node_disk_bytes"
NODE_CREATED = "exordos_compute_node_created_timestamp_seconds"

SET_INFO = "exordos_compute_set_info"
SET_STATUS = "exordos_compute_set_status"
SET_REPLICAS = "exordos_compute_set_replicas"
SET_CORES = "exordos_compute_set_cores"
SET_RAM = "exordos_compute_set_ram_bytes"
SET_DISK = "exordos_compute_set_disk_bytes"
SET_CREATED = "exordos_compute_set_created_timestamp_seconds"

POOL_STATUS = "exordos_compute_pool_status"
POOL_CORES = "exordos_compute_pool_cores"
POOL_CORES_AVAILABLE = "exordos_compute_pool_cores_available"
POOL_RAM = "exordos_compute_pool_ram_bytes"
POOL_RAM_AVAILABLE = "exordos_compute_pool_ram_available_bytes"

MIB = 1024**2
GIB = 1024**3


def _disk_sizes(disk_spec: dict | None) -> list[int]:
    """Sizes, in GiB, of the disks of a node or of every node of a set."""
    if not disk_spec:
        return []
    if "disks" in disk_spec:
        return [d.get("size", 0) for d in disk_spec["disks"]]
    return [disk_spec.get("size", 0)]


def _image(disk_spec: dict | None) -> str:
    """Image of the root disk."""
    if not disk_spec:
        return ""
    if "disks" in disk_spec:
        disks = disk_spec["disks"]
        return (disks[0].get("image") or "") if disks else ""
    return disk_spec.get("image") or ""


def _format_disks(sizes: list[int]) -> str:
    return ", ".join(f"{s}G" for s in sizes)


class NodesCollector(base.AbstractCollector):
    """Every node with what the node dashboard shows about it.

    The status is a series of its own, labelled with what never changes, so
    renaming the node or changing its IP does not split its history. The
    hostname follows the rule of the machine builder, so it matches the
    `instance` of the node_exporter metrics and the host of the logs.
    """

    def collect(self, session) -> tp.Iterable[base.Sample]:
        rows = session.execute(
            "SELECT n.uuid, n.name, COALESCE(n.hostname, n.name) AS hostname,"
            " n.description, n.project_id, n.status, n.node_type, n.cores,"
            " n.ram, n.disk_spec, EXTRACT(EPOCH FROM n.created_at) AS created_at,"
            " n.node_set, s.name AS set_name, e.name AS element,"
            " p.ipv4, mp.name AS pool"
            f" FROM {compute_models.Node.__tablename__} n"
            f" LEFT JOIN {compute_models.NodeSet.__tablename__} s"
            " ON s.uuid = n.node_set"
            # A node of a set belongs to the element of the set
            f" LEFT JOIN {em_models.Resource.__tablename__} r"
            " ON r.uuid = COALESCE(n.node_set, n.uuid)"
            f" LEFT JOIN {em_models.Element.__tablename__} e ON e.uuid = r.element"
            " LEFT JOIN (SELECT node, string_agg(ipv4, ', ' ORDER BY ipv4) AS ipv4"
            f" FROM {compute_models.Port.__tablename__}"
            " WHERE node IS NOT NULL AND ipv4 IS NOT NULL GROUP BY node) p"
            " ON p.node = n.uuid"
            " LEFT JOIN (SELECT DISTINCT ON (node) node, pool"
            f" FROM {compute_models.Machine.__tablename__}"
            " WHERE node IS NOT NULL ORDER BY node, created_at) m"
            " ON m.node = n.uuid"
            f" LEFT JOIN {compute_models.MachinePool.__tablename__} mp"
            " ON mp.uuid = m.pool"
        ).fetchall()
        for row in rows:
            node_uuid = str(row["uuid"])
            set_uuid = str(row["node_set"] or "")
            sizes = _disk_sizes(row["disk_spec"])
            yield base.Sample(
                NODE_INFO,
                {
                    "node_uuid": node_uuid,
                    "node": row["name"],
                    "hostname": row["hostname"],
                    "description": row["description"],
                    "project_id": str(row["project_id"]),
                    "node_type": row["node_type"],
                    "set": row["set_name"] or "",
                    "set_uuid": set_uuid,
                    "element": row["element"] or "",
                    "image": _image(row["disk_spec"]),
                    "disks": _format_disks(sizes),
                    "ipv4": row["ipv4"] or "",
                    "pool": row["pool"] or "",
                },
                1,
            )
            yield base.Sample(
                NODE_STATUS,
                {
                    "node_uuid": node_uuid,
                    "set_uuid": set_uuid,
                    "element": row["element"] or "",
                },
                NODE_STATUS_CODES[row["status"]],
            )
            ident = {"node_uuid": node_uuid}
            yield base.Sample(NODE_CORES, ident, row["cores"])
            yield base.Sample(NODE_RAM, ident, row["ram"] * MIB)
            yield base.Sample(NODE_DISK, ident, sum(sizes) * GIB)
            yield base.Sample(NODE_CREATED, ident, float(row["created_at"]))


class SetsCollector(base.AbstractCollector):
    """Every node set; cores, RAM and disks are the ones of each node."""

    def collect(self, session) -> tp.Iterable[base.Sample]:
        rows = session.execute(
            "SELECT s.uuid, s.name, s.description, s.project_id, s.status,"
            " s.node_type, s.cores, s.ram, s.replicas, s.disk_spec,"
            " EXTRACT(EPOCH FROM s.created_at) AS created_at, e.name AS element"
            f" FROM {compute_models.NodeSet.__tablename__} s"
            f" LEFT JOIN {em_models.Resource.__tablename__} r ON r.uuid = s.uuid"
            f" LEFT JOIN {em_models.Element.__tablename__} e ON e.uuid = r.element"
        ).fetchall()
        for row in rows:
            set_uuid = str(row["uuid"])
            sizes = _disk_sizes(row["disk_spec"])
            yield base.Sample(
                SET_INFO,
                {
                    "set_uuid": set_uuid,
                    "set": row["name"],
                    "description": row["description"],
                    "project_id": str(row["project_id"]),
                    "node_type": row["node_type"],
                    "element": row["element"] or "",
                    "image": _image(row["disk_spec"]),
                    "disks": _format_disks(sizes),
                },
                1,
            )
            yield base.Sample(
                SET_STATUS,
                {
                    "set_uuid": set_uuid,
                    "element": row["element"] or "",
                },
                NODE_STATUS_CODES[row["status"]],
            )
            ident = {"set_uuid": set_uuid}
            yield base.Sample(SET_REPLICAS, ident, row["replicas"])
            yield base.Sample(SET_CORES, ident, row["cores"])
            yield base.Sample(SET_RAM, ident, row["ram"] * MIB)
            yield base.Sample(SET_DISK, ident, sum(sizes) * GIB)
            yield base.Sample(SET_CREATED, ident, float(row["created_at"]))


class PoolsCollector(base.AbstractCollector):
    """Capacity of the machine pools, as the scheduler sees it."""

    def collect(self, session) -> tp.Iterable[base.Sample]:
        rows = session.execute(
            "SELECT uuid, name, status, all_cores, avail_cores, all_ram, avail_ram"
            f" FROM {compute_models.MachinePool.__tablename__}"
        ).fetchall()
        for row in rows:
            labels = {"pool_uuid": str(row["uuid"]), "pool": row["name"]}
            yield base.Sample(POOL_STATUS, labels, POOL_STATUS_CODES[row["status"]])
            yield base.Sample(POOL_CORES, labels, row["all_cores"])
            yield base.Sample(POOL_CORES_AVAILABLE, labels, row["avail_cores"])
            yield base.Sample(POOL_RAM, labels, row["all_ram"] * MIB)
            yield base.Sample(POOL_RAM_AVAILABLE, labels, row["avail_ram"] * MIB)
