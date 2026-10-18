# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy at http://www.apache.org/licenses/LICENSE-2.0

import json
from pathlib import Path
import runpy
import uuid

import pytest
from restalchemy.dm import filters
from restalchemy.storage.sql import engines

from exordos_core.compute.dm import models as compute


@pytest.mark.parametrize("node_type", ["VM", "HW"])
@pytest.mark.parametrize("source", [None, "custom-bridge"])
def test_upgrade_from_master_preserves_flat_entities(user_api, node_type, source):
    step = runpy.run_path(
        str(Path(__file__).parents[5] / "migrations/0010-network-sdn-9e41c7.py")
    )["migration_step"]
    network, subnet, node, machine, nodeset, pinned_set = [
        str(uuid.uuid4()) for _ in range(6)
    ]
    ports = [str(uuid.uuid4()) for _ in range(3)]
    project = str(uuid.UUID(int=0))
    driver = {"driver": "flat_bridge", "path": "/tmp/sdn-migration-flat"}
    with engines.engine_factory.get_engine().session_manager() as session:
        step.downgrade(session)
        assert not session.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
            "AND table_name = 'compute_ports' AND column_name = 'owner_node'"
        ).fetchone()
        session.execute(
            "INSERT INTO compute_networks (uuid, project_id, name, description, driver_spec) "
            "VALUES (%s, %s, 'flat', '', %s::jsonb)",
            (network, project, json.dumps(driver)),
        )
        session.execute(
            "INSERT INTO compute_subnets (uuid, project_id, network, name, description, cidr, next_server) "
            "VALUES (%s, %s, %s, 'eno1', '', '10.210.0.0/24', NULL)",
            (subnet, project, network),
        )
        session.execute(
            "INSERT INTO nodes (uuid, project_id, name, description, cores, ram, node_type, status, disk_spec, default_network) "
            "VALUES (%s, %s, 'legacy', '', 1, 1024, %s, 'ACTIVE', %s::jsonb, %s)",
            (
                node,
                project,
                node_type,
                json.dumps({"kind": "root_disk", "image": "ubuntu_24.04"}),
                json.dumps(
                    {
                        "port": ports[0],
                        "subnet": subnet,
                        "ipv4": "10.210.0.10",
                        "target_ipv4": "10.210.0.10",
                    }
                ),
            ),
        )
        session.execute(
            "INSERT INTO machines (uuid, project_id, node, name, description, cores, ram, machine_type, boot, status) "
            "VALUES (%s, %s, %s, 'legacy', '', 1, 1024, %s, 'hd0', 'ACTIVE')",
            (machine, project, node, node_type),
        )
        for identifier, template in ((nodeset, {}), (pinned_set, {"network": network})):
            session.execute(
                "INSERT INTO compute_sets (uuid, project_id, name, description, cores, ram, replicas, node_type, set_type, status, nodes, default_network) "
                "VALUES (%s, %s, 'legacy-set', '', 1, 1024, 1, 'VM', 'SET', 'ACTIVE', '{}'::jsonb, %s::jsonb)",
                (identifier, project, json.dumps(template)),
            )
        for index, identifier in enumerate(ports):
            session.execute(
                "INSERT INTO compute_ports (uuid, project_id, subnet, node, machine, name, description, ipv4, mac, status, source, created_at) "
                "VALUES (%s, %s, %s, %s, %s, 'legacy-port', '', %s, %s, 'ACTIVE', %s, NOW() + %s * INTERVAL '1 second')",
                (
                    identifier,
                    project,
                    subnet,
                    node if index < 2 else None,
                    machine if index < 2 else None,
                    f"10.210.0.{10 + index}",
                    f"52:54:00:00:00:{10 + index:02x}",
                    source,
                    index,
                ),
            )
        before = session.execute(
            "SELECT uuid, subnet, node, machine, ipv4, mac, status FROM compute_ports WHERE uuid = ANY(%s::uuid[]) ORDER BY uuid",
            (ports,),
        ).fetchall()
        step.upgrade(session)
        assert (
            session.execute(
                "SELECT default_network FROM nodes WHERE uuid = %s", (node,)
            ).fetchone()["default_network"]
            is None
        )
        assert (
            session.execute(
                "SELECT uuid, subnet, node, machine, ipv4, mac, status FROM compute_ports WHERE uuid = ANY(%s::uuid[]) ORDER BY uuid",
                (ports,),
            ).fetchall()
            == before
        )
        for index, identifier in enumerate(ports):
            port = session.execute(
                "SELECT * FROM compute_ports WHERE uuid = %s", (identifier,)
            ).fetchone()
            assert port["index"] == (index if index < 2 else None)
            assert port["owner_node"] == (uuid.UUID(node) if index < 2 else None)
            assert port["source"] == (source or "eno1")
            assert port["enforce_security_group"] is False
        assert (
            session.execute(
                "SELECT driver_spec FROM compute_networks WHERE uuid = %s", (network,)
            ).fetchone()["driver_spec"]
            == driver
        )
        assert session.execute(
            "SELECT status, boot FROM machines WHERE uuid = %s", (machine,)
        ).fetchone() == {"status": "ACTIVE", "boot": "hd0"}
        assert (
            session.execute(
                "SELECT default_network FROM compute_sets WHERE uuid = %s", (nodeset,)
            ).fetchone()["default_network"]
            is None
        )
        assert session.execute(
            "SELECT default_network FROM compute_sets WHERE uuid = %s", (pinned_set,)
        ).fetchone()["default_network"] == {"network": network}
        assert not session.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND "
            "column_name IN ('networking', 'network_activation_barrier', 'provenance', 'origin', 'interfaces', 'generated_config', 'effective_config')"
        ).fetchone()

    saved = compute.Node.objects.get_one(filters={"uuid": filters.EQ(node)})
    compute.Node.project_observations([saved])
    assert saved.default_network["ipv4"] == "10.210.0.10"
    assert saved.default_network["port"] == ports[0]
    with engines.engine_factory.get_engine().session_manager() as session:
        step.downgrade(session)
        snapshot = json.loads(
            session.execute(
                "SELECT default_network FROM nodes WHERE uuid = %s", (node,)
            ).fetchone()["default_network"]
        )
        assert snapshot["port"] == ports[0] and snapshot["ipv4"] == "10.210.0.10"
        step.upgrade(session)
    saved = compute.Node.objects.get_one(filters={"uuid": filters.EQ(node)})
    port = compute.Port.objects.get_one(filters={"uuid": filters.EQ(ports[0])})
    port.node = None
    port.update()
    compute.Node.project_observations([saved])
    # Port detachment must not expose the released master's cached address.
    assert "port" not in saved.default_network
    assert "ipv4" not in saved.default_network
    assert "target_ipv4" not in saved.default_network
    assert "subnet" not in saved.default_network
