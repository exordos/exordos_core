# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy at http://www.apache.org/licenses/LICENSE-2.0

import json
from pathlib import Path
import runpy
import uuid

from gcl_sdk.agents.universal import utils
from gcl_sdk.agents.universal.dm import models as ua
from gcl_sdk.agents.universal.drivers.pool import MetaPort
import pytest
from restalchemy.common import contexts
from restalchemy.dm import filters
from restalchemy.storage.sql import engines

from exordos_core.common import constants as c
from exordos_core.compute.builders.pool import PoolBuilderService
from exordos_core.compute.pool.dm import models


@pytest.fixture
def topology(user_api):
    ids = {
        key: uuid.uuid4()
        for key in (
            "pool",
            "node",
            "machine",
            "network",
            "subnet",
            "primary",
            "secondary",
        )
    }
    with engines.engine_factory.get_engine().session_manager() as session:
        session.execute(
            "INSERT INTO machine_pools (uuid, name, description, driver_spec, machine_type, status) "
            "VALUES (%s,'pool','',%s::jsonb,'VM','ACTIVE')",
            (str(ids["pool"]), json.dumps({"kind": "dummy"})),
        )
        session.execute(
            "INSERT INTO nodes (uuid,project_id,name,description,cores,ram,node_type,status,disk_spec) "
            "VALUES (%s,%s,'guest','',1,1024,'VM','ACTIVE','{}'::jsonb)",
            (str(ids["node"]), str(c.ZERO_UUID)),
        )
        session.execute(
            "INSERT INTO machines (uuid,project_id,node,pool,name,description,cores,ram,machine_type,boot,status) "
            "VALUES (%s,%s,%s,%s,'guest','',1,1024,'VM','hd0','ACTIVE')",
            tuple(
                str(ids[key]) if key != "project" else str(c.ZERO_UUID)
                for key in ("machine", "project", "node", "pool")
            ),
        )
        session.execute(
            "INSERT INTO compute_networks (uuid,project_id,name,description,driver_spec) "
            "VALUES (%s,%s,'tenant','',%s::jsonb)",
            (str(ids["network"]), str(c.ZERO_UUID), json.dumps({"driver": "ovs_evpn"})),
        )
        session.execute(
            "INSERT INTO compute_subnets (uuid,project_id,network,name,description,cidr,next_server) "
            "VALUES (%s,%s,%s,'tenant','', '10.91.0.0/24',NULL)",
            (str(ids["subnet"]), str(c.ZERO_UUID), str(ids["network"])),
        )
        for index, key in enumerate(("primary", "secondary")):
            session.execute(
                "INSERT INTO compute_ports (uuid,project_id,subnet,node,name,description,mac,status,source,index) "
                "VALUES (%s,%s,%s,%s,'nic','',%s,'ACTIVE','tenant',%s)",
                (
                    str(ids[key]),
                    str(c.ZERO_UUID),
                    str(ids["subnet"]),
                    str(ids["node"]),
                    f"52:54:00:00:00:{index:02x}",
                    index,
                ),
            )
    yield ids
    with engines.engine_factory.get_engine().session_manager() as session:
        session.execute(
            "DELETE FROM ua_target_resources WHERE uuid=ANY(%s::uuid[])",
            ([str(value) for value in ids.values()],),
        )
        session.execute(
            "DELETE FROM compute_ports WHERE uuid=ANY(%s::uuid[])",
            ([str(ids["primary"]), str(ids["secondary"])],),
        )
        session.execute("DELETE FROM nodes WHERE uuid=%s", (str(ids["node"]),))
        session.execute(
            "DELETE FROM compute_subnets WHERE uuid=%s", (str(ids["subnet"]),)
        )
        session.execute(
            "DELETE FROM compute_networks WHERE uuid=%s", (str(ids["network"]),)
        )
        session.execute("DELETE FROM machine_pools WHERE uuid=%s", (str(ids["pool"]),))


def fetch_port(identifier):
    return models.PoolPort.objects.get_one(filters={"uuid": filters.EQ(identifier)})


def test_port_changes_have_independent_targets_and_builder_lifecycle(topology):
    ids = topology
    builder = PoolBuilderService(uuid=uuid.uuid4(), orch_client=None)
    port = fetch_port(ids["primary"])
    builder._instance_model = models.PoolPort
    builder._iteration_context = {
        "clause_filters": {"pools": []},
    }
    builder._iteration_context["clause_filters"]["pools"] = [
        models.Pool.objects.get_one(filters={"uuid": filters.EQ(ids["pool"])})
    ]
    with contexts.Context().session_manager():
        builder._actualize_new_instance(port)
    before = ua.TargetResource.objects.get_one(
        filters={"kind": filters.EQ("pool_port"), "uuid": filters.EQ(port.uuid)}
    )
    original_hash = before.hash
    assert before.value["attached"] is True
    assert before.value["machine"] == str(ids["machine"])
    with engines.engine_factory.get_engine().session_manager() as session:
        machine_before = session.execute(
            "SELECT updated_at FROM machines WHERE uuid=%s", (str(ids["machine"]),)
        ).fetchone()["updated_at"]
        session.execute(
            "UPDATE compute_ports SET mac='52:54:00:ff:ff:ff', updated_at=NOW() WHERE uuid=%s",
            (str(port.uuid),),
        )
    updated = models.PoolPort.get_updated_instances({"pool": filters.In([ids["pool"]])})
    assert [item.uuid for item in updated] == [port.uuid]
    with contexts.Context().session_manager():
        builder._actualize_updated_instance(updated[0], before)
    target = ua.TargetResource.objects.get_one(
        filters={"res_uuid": filters.EQ(before.res_uuid)}
    )
    assert target.value["mac"] == "52:54:00:ff:ff:ff"
    assert target.hash != original_hash
    actual = MetaPort.from_ua_resource(target).to_ua_resource("pool_port")
    with contexts.Context().session_manager():
        builder._actualize_outdated_instance(fetch_port(port.uuid), target, actual)
    settled = ua.TargetResource.objects.get_one(
        filters={"res_uuid": filters.EQ(target.res_uuid)}
    )
    assert settled.full_hash == actual.full_hash
    assert (
        models.PoolPort.get_updated_instances({"pool": filters.In([ids["pool"]])}) == []
    )

    with engines.engine_factory.get_engine().session_manager() as session:
        assert (
            session.execute(
                "SELECT updated_at FROM machines WHERE uuid=%s", (str(ids["machine"]),)
            ).fetchone()["updated_at"]
            == machine_before
        )
        session.execute(
            "UPDATE compute_ports SET node=NULL WHERE uuid=%s", (str(port.uuid),)
        )
    assert [item.uuid for item in models.PoolPort.get_deleted_instances()] == [
        port.uuid
    ]


def test_primary_boot_port_keeps_uuid_and_mac_while_source_changes(topology):
    ids = topology
    primary = fetch_port(ids["primary"])
    with engines.engine_factory.get_engine().session_manager() as session:
        boot = session.execute(
            "SELECT name FROM compute_subnets WHERE next_server IS NOT NULL LIMIT 1"
        ).fetchone()
        # A test database need not have a boot network seeded.
        if boot is None:
            session.execute(
                "UPDATE compute_subnets SET next_server='10.91.0.1' WHERE uuid=%s",
                (str(ids["subnet"]),),
            )
            boot = {"name": "tenant"}
        session.execute(
            "UPDATE compute_networks SET driver_spec=%s::jsonb WHERE uuid=%s",
            (json.dumps({"driver": "flat_bridge"}), str(ids["network"])),
        )
        session.execute(
            "UPDATE compute_ports SET source='main-net' WHERE node=%s",
            (str(ids["node"]),),
        )
        session.execute(
            "UPDATE machines SET boot='network', updated_at=NOW() WHERE uuid=%s",
            (str(ids["machine"]),),
        )
    boot_port = fetch_port(ids["primary"])
    assert boot_port.source == boot["name"]
    assert (boot_port.uuid, boot_port.mac) == (primary.uuid, primary.mac)
    assert fetch_port(ids["secondary"]).source == "main-net"
    with engines.engine_factory.get_engine().session_manager() as session:
        session.execute(
            "UPDATE machines SET boot='hd0', updated_at=NOW() WHERE uuid=%s",
            (str(ids["machine"]),),
        )
    assert fetch_port(ids["primary"]).source == "main-net"


def test_ovs_backfill_preserves_explicit_values_and_migrates_machine_targets(user_api):
    path = Path(__file__).parents[4] / "migrations/0010-network-sdn-9e41c7.py"
    step = runpy.run_path(str(path))["migration_step"]
    pool_ids = [uuid.uuid4() for _ in range(5)]
    specs = [
        {"kind": "libvirt"},
        {"kind": "exordos_local_hyper"},
        {"kind": "libvirt", "ovs": True},
        {"kind": "exordos_local_hyper", "ovs": False},
        {"kind": "dummy"},
    ]
    machine_id = uuid.uuid4()
    old_value = {
        "uuid": str(machine_id),
        "port_info": {"mac": "52:54:00:00:00:01"},
        "cores": 2,
        "ram": 2048,
    }
    target = ua.TargetResource(
        uuid=machine_id,
        kind="pool_machine",
        res_uuid=ua.Resource.gen_res_uuid(machine_id, "pool_machine"),
        value=old_value,
        hash=utils.calculate_hash(old_value),
    )
    target.save()
    with engines.engine_factory.get_engine().session_manager() as session:
        step.downgrade(session)
        for identifier, spec in zip(pool_ids, specs):
            session.execute(
                "INSERT INTO machine_pools (uuid,name,description,driver_spec,machine_type,status,updated_at) "
                "VALUES (%s,'legacy','',%s::jsonb,'VM','ACTIVE','2000-01-01')",
                (str(identifier), json.dumps(spec)),
            )
        step.upgrade(session)
        for identifier, expected in zip(pool_ids, [False, False, True, False, None]):
            row = session.execute(
                "SELECT driver_spec,updated_at FROM machine_pools WHERE uuid=%s",
                (str(identifier),),
            ).fetchone()
            assert row["driver_spec"].get("ovs") is expected
            assert row["updated_at"].year > 2000
        migrated = session.execute(
            "SELECT value,hash FROM ua_target_resources WHERE res_uuid=%s",
            (str(target.res_uuid),),
        ).fetchone()
        expected = {
            key: value for key, value in old_value.items() if key != "port_info"
        }
        assert migrated["value"] == expected
        assert migrated["hash"] == utils.calculate_hash(expected)
        session.execute(
            "DELETE FROM machine_pools WHERE uuid=ANY(%s::uuid[])",
            ([str(identifier) for identifier in pool_ids],),
        )
        session.execute(
            "DELETE FROM ua_target_resources WHERE res_uuid=%s", (str(target.res_uuid),)
        )


def test_boot_network_set_changes_update_targets_without_touching_the_machine(topology):
    ids = topology
    older, newer = uuid.uuid4(), uuid.uuid4()
    with engines.engine_factory.get_engine().session_manager() as session:
        saved_boot = session.execute(
            "SELECT uuid,next_server FROM compute_subnets WHERE next_server IS NOT NULL"
        ).fetchall()
        session.execute(
            "UPDATE compute_subnets SET next_server=NULL WHERE next_server IS NOT NULL"
        )
        session.execute(
            "UPDATE compute_networks SET driver_spec=%s::jsonb WHERE uuid=%s",
            (json.dumps({"driver": "flat_bridge"}), str(ids["network"])),
        )
        session.execute(
            "UPDATE machines SET boot='network',updated_at=NOW() WHERE uuid=%s",
            (str(ids["machine"]),),
        )
        session.execute(
            "INSERT INTO compute_subnets (uuid,project_id,network,name,description,cidr,next_server) VALUES (%s,%s,%s,'older-boot','','10.92.0.0/24','10.92.0.1')",
            (str(older), str(c.ZERO_UUID), str(ids["network"])),
        )
        original_stamp = session.execute(
            "SELECT updated_at FROM machines WHERE uuid=%s", (str(ids["machine"]),)
        ).fetchone()["updated_at"]
    try:
        builder = PoolBuilderService(uuid=uuid.uuid4(), orch_client=None)
        builder._instance_model = models.PoolPort
        builder._iteration_context = {
            "clause_filters": {
                "pools": [
                    models.Pool.objects.get_one(
                        filters={"uuid": filters.EQ(ids["pool"])}
                    )
                ]
            }
        }
        primary = fetch_port(ids["primary"])
        assert primary.source == "older-boot"
        with contexts.Context().session_manager():
            builder._actualize_new_instance(primary)
        target = ua.TargetResource.objects.get_one(
            filters={"uuid": filters.EQ(primary.uuid), "kind": filters.EQ("pool_port")}
        )
        with engines.engine_factory.get_engine().session_manager() as session:
            session.execute(
                "INSERT INTO compute_subnets (uuid,project_id,network,name,description,cidr,next_server) VALUES (%s,%s,%s,'newer-boot','','10.93.0.0/24','10.93.0.1')",
                (str(newer), str(c.ZERO_UUID), str(ids["network"])),
            )
        clause = {"pool": filters.In([ids["pool"]])}
        pending = models.PoolPort.get_updated_instances(clause)
        assert [item.uuid for item in pending] == [primary.uuid]
        assert pending[0].source is None
        with contexts.Context().session_manager():
            builder._actualize_updated_instance(pending[0], target)
        assert models.PoolPort.get_updated_instances(clause) == []
        with engines.engine_factory.get_engine().session_manager() as session:
            session.execute("DELETE FROM compute_subnets WHERE uuid=%s", (str(older),))
        ready = models.PoolPort.get_updated_instances(clause)
        assert [item.uuid for item in ready] == [primary.uuid]
        assert ready[0].source == "newer-boot"
        with contexts.Context().session_manager():
            builder._actualize_updated_instance(ready[0], target)
        assert models.PoolPort.get_updated_instances(clause) == []
        with engines.engine_factory.get_engine().session_manager() as session:
            assert (
                session.execute(
                    "SELECT updated_at FROM machines WHERE uuid=%s",
                    (str(ids["machine"]),),
                ).fetchone()["updated_at"]
                == original_stamp
            )
    finally:
        with engines.engine_factory.get_engine().session_manager() as session:
            session.execute(
                "DELETE FROM compute_subnets WHERE uuid=ANY(%s::uuid[])",
                ([str(older), str(newer)],),
            )
            for boot in saved_boot:
                session.execute(
                    "UPDATE compute_subnets SET next_server=%s WHERE uuid=%s",
                    (boot["next_server"], boot["uuid"]),
                )


def test_flat_machine_targets_survive_migration_roundtrip(topology):
    ids = topology
    path = Path(__file__).parents[4] / "migrations/0010-network-sdn-9e41c7.py"
    step = runpy.run_path(str(path))["migration_step"]
    value = {
        "uuid": str(ids["machine"]),
        "pool": str(ids["pool"]),
        "project_id": str(c.ZERO_UUID),
        "cores": 1,
        "ram": 1024,
        "boot": "hd0",
    }
    target = ua.TargetResource(
        uuid=ids["machine"],
        kind="pool_machine",
        res_uuid=ua.Resource.gen_res_uuid(ids["machine"], "pool_machine"),
        value=value,
        hash=utils.calculate_hash(value),
    )
    target.save()
    pool_spec = {"kind": "libvirt", "connection_uri": "qemu:///system", "ovs": False}
    pool_value = {"uuid": str(ids["pool"]), "driver_spec": pool_spec}
    pool_target = ua.TargetResource(
        uuid=ids["pool"],
        kind="pool",
        res_uuid=ua.Resource.gen_res_uuid(ids["pool"], "pool"),
        value=pool_value,
        hash=utils.calculate_hash(pool_value),
    )
    pool_target.save()
    port = fetch_port(ids["primary"])
    port.to_ua_resource().save()
    MetaPort.from_ua_resource(port.to_ua_resource()).to_ua_resource("pool_port").save()
    with engines.engine_factory.get_engine().session_manager() as session:
        session.execute(
            "UPDATE machine_pools SET driver_spec=%s::jsonb WHERE uuid=%s",
            (json.dumps(pool_spec), str(ids["pool"])),
        )
        session.execute(
            "UPDATE compute_networks SET driver_spec=%s::jsonb WHERE uuid=%s",
            (json.dumps({"driver": "flat_bridge"}), str(ids["network"])),
        )
        session.execute(
            "UPDATE compute_ports SET created_at=created_at + INTERVAL '1 second' WHERE uuid=%s",
            (str(ids["secondary"]),),
        )
        stamp = session.execute(
            "SELECT updated_at FROM machines WHERE uuid=%s", (str(ids["machine"]),)
        ).fetchone()["updated_at"]
        step.downgrade(session)
        legacy = session.execute(
            "SELECT value,hash,full_hash FROM ua_target_resources WHERE res_uuid=%s",
            (str(target.res_uuid),),
        ).fetchone()
        expected_port = {
            "ipv4": None,
            "mask": None,
            "mac": port.mac,
            "source": "tenant",
        }
        assert legacy["value"] == value | {"port_info": expected_port}
        assert legacy["hash"] == utils.calculate_hash(legacy["value"])
        assert legacy["full_hash"] == ""
        assert (
            session.execute(
                "SELECT 1 FROM ua_target_resources WHERE kind='pool_port'"
            ).fetchone()
            is None
        )
        assert (
            session.execute(
                "SELECT 1 FROM ua_actual_resources WHERE kind='pool_port'"
            ).fetchone()
            is None
        )
        spec = session.execute(
            "SELECT driver_spec FROM machine_pools WHERE uuid=%s", (str(ids["pool"]),)
        ).fetchone()["driver_spec"]
        assert "ovs" not in spec
        legacy_pool = session.execute(
            "SELECT value,hash FROM ua_target_resources WHERE res_uuid=%s",
            (str(pool_target.res_uuid),),
        ).fetchone()
        assert "ovs" not in legacy_pool["value"]["driver_spec"]
        assert legacy_pool["hash"] == utils.calculate_hash(legacy_pool["value"])
        step.upgrade(session)
        restored = session.execute(
            "SELECT value,hash FROM ua_target_resources WHERE res_uuid=%s",
            (str(target.res_uuid),),
        ).fetchone()
        assert restored["value"] == value
        assert restored["hash"] == utils.calculate_hash(value)
        assert (
            session.execute(
                "SELECT updated_at FROM machines WHERE uuid=%s", (str(ids["machine"]),)
            ).fetchone()["updated_at"]
            == stamp
        )
