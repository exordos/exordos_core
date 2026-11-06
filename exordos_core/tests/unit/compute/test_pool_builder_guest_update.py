# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License").

import types
from unittest import mock
import uuid

import pytest
from restalchemy.storage.sql import orm

from exordos_core.compute.builders import pool as builder


@pytest.fixture
def guest_update(monkeypatch):
    service = builder.PoolBuilderService(uuid.uuid4(), mock.Mock())
    node_uuid = uuid.uuid4()
    machine = types.SimpleNamespace(
        uuid=node_uuid,
        name="backend-test",
        project_id=uuid.uuid4(),
        cores=2,
        ram=1024,
        machine_type="VM",
        pool=types.SimpleNamespace(uuid=uuid.uuid4()),
        node=types.SimpleNamespace(uuid=node_uuid, hostname=None, name="backend-test"),
    )
    main_port, boot_port = (
        types.SimpleNamespace(ipv4=None, mask=None, mac=None, source=name)
        for name in ("main-network", "boot-network")
    )
    volume = types.SimpleNamespace(image="urn:images:test")
    monkeypatch.setattr(
        orm.ObjectCollection,
        "get_one_or_none",
        lambda *args, **kwargs: types.SimpleNamespace(uuid=node_uuid),
    )
    with (
        mock.patch.object(
            service, "_get_or_fetch_machine_ctx", return_value=(main_port, volume)
        ),
        mock.patch.object(service, "_resolve_image", return_value="new-image"),
        mock.patch.object(service, "_agent_by_pool", return_value=uuid.uuid4()),
        mock.patch.object(
            builder.models.Port, "from_boot_network", return_value=boot_port
        ),
    ):
        yield service, machine


@pytest.mark.parametrize("core_machine", [False, True])
@pytest.mark.parametrize("target_boot", ["hd0", "network"])
@pytest.mark.parametrize(
    "reported_image,target_image",
    [
        (None, "old-image"),
        (None, "new-image"),
        ("old-image", "old-image"),
        ("new-image", "new-image"),
    ],
)
def test_image_update_without_guest_report_uses_network_boot(
    guest_update, core_machine, target_boot, reported_image, target_image
):
    service, machine = guest_update
    if core_machine:
        machine.name = "core-set-node-test"
    actual = (
        types.SimpleNamespace(image=reported_image)
        if reported_image is not None
        else None
    )
    pool, guest = service._actualize_machine_derivatives_on_create_update(
        machine,
        machine_guest_pair=(
            builder.pool_models.GuestMachine(image=target_image, boot=target_boot),
            actual,
        ),
    )
    known_image = reported_image if reported_image is not None else target_image
    network_boot = not core_machine and (
        known_image != "new-image" or target_boot == "network"
    )
    assert guest.boot == pool.boot == ("network" if network_boot else "hd0")
    assert pool.port_info["source"] == (
        "boot-network" if network_boot else "main-network"
    )


@pytest.mark.parametrize("core_machine", [False, True])
def test_initial_creation_boots_on_the_correct_network(guest_update, core_machine):
    service, machine = guest_update
    if core_machine:
        machine.name = "core-set-node-test"
    pool, guest = service.create_instance_derivatives(machine)
    assert pool.boot == guest.boot == ("hd0" if core_machine else "network")
    assert pool.port_info["source"] == (
        "main-network" if core_machine else "boot-network"
    )


@pytest.mark.parametrize("change", ["cores", "ram", "hostname"])
@pytest.mark.parametrize("reported_status", [None, "NEW", "IN_PROGRESS", "ACTIVE"])
def test_pending_network_boot_survives_updates_until_flashed(
    guest_update, change, reported_status
):
    service, machine = guest_update
    pool, guest = service._actualize_machine_derivatives_on_create_update(
        machine,
        machine_guest_pair=(
            builder.pool_models.GuestMachine(image="old-image", boot="hd0"),
            None,
        ),
    )
    assert pool.boot == guest.boot == "network"
    assert pool.port_info["source"] == "boot-network"

    if change == "hostname":
        machine.node.hostname = "updated-node"
    else:
        setattr(machine, change, getattr(machine, change) * 2)
    actual = (
        builder.pool_models.GuestMachine(
            image="new-image", boot="network", status=reported_status
        )
        if reported_status is not None
        else None
    )
    pool, guest = service.update_instance_derivatives(
        machine, None, [(pool, None), (guest, actual)]
    )
    assert pool.boot == guest.boot == machine.boot == "network"
    assert pool.port_info["source"] == "boot-network"
    assert (pool.cores, pool.ram, guest.hostname) == (
        machine.cores,
        machine.ram,
        machine.node.hostname or machine.node.name,
    )

    pool, guest = service._actualize_machine_derivatives_on_outdate(
        machine, [(pool, None), (guest, actual)]
    )
    assert pool.boot == guest.boot == "network"
    assert pool.port_info["source"] == "boot-network"

    flashed = builder.pool_models.GuestMachine(
        image="new-image", boot="network", status="FLASHED"
    )
    pool, guest = service._actualize_machine_derivatives_on_outdate(
        machine, [(pool, None), (guest, flashed)]
    )
    assert pool.boot == guest.boot == machine.boot == "hd0"
    assert pool.port_info["source"] == "main-network"

    pool, guest = service.update_instance_derivatives(
        machine, None, [(pool, None), (guest, None)]
    )
    assert pool.boot == guest.boot == "hd0"
    assert pool.port_info["source"] == "main-network"
