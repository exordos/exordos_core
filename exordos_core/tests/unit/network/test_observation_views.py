# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0.
"""Serializing a model never loads its observed network relationships."""

import uuid

import pytest
from restalchemy.storage.sql import engines
from gcl_sdk.infra.dm import models as infra_models

from exordos_core.compute.dm import models as compute
from exordos_core.user_api.network.dm import models as catalog


@pytest.mark.parametrize("restored", [False, True])
@pytest.mark.parametrize(
    "model", [compute.Port, catalog.Address, catalog.SecurityGroup, compute.Node]
)
def test_raw_model_serialization_does_not_read_database(monkeypatch, model, restored):
    value = {"project_id": uuid.uuid4()}
    if model in (compute.Port, catalog.Address):
        value["subnet"] = uuid.uuid4()
    if model is catalog.Address:
        value.update(kind="floating", port=uuid.uuid4(), address="203.0.113.7")
    if model is compute.Node:
        value.update(
            cores=1, ram=1024, disk_spec=infra_models.RootDiskSpec(image="ubuntu_24.04")
        )
    obj = model(**value)
    if restored:
        obj = model.restore_row(obj._get_prepared_data())
    obj._saved = True

    def forbidden(*args, **kwargs):
        raise AssertionError("serialization attempted an implicit SQL read")

    monkeypatch.setattr(engines.engine_factory, "get_engine", forbidden)
    obj.dump_to_simple_view()


@pytest.mark.parametrize(
    "condition,expected",
    [
        ("current", "ACTIVE"),
        ("wrong_owner", "IN_PROGRESS"),
        ("missing_agent", "IN_PROGRESS"),
        ("wrong_hash", "IN_PROGRESS"),
        ("wrong_ip", "IN_PROGRESS"),
        ("error", "ERROR"),
    ],
)
def test_address_status_checks_owned_current_report_without_sql(
    monkeypatch, condition, expected
):
    from gcl_sdk.agents.universal.dm import models as ua
    from exordos_core.network.views import address_status

    port, owner_node, owner = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    address = catalog.Address(
        project_id=uuid.uuid4(),
        subnet=uuid.uuid4(),
        kind="floating",
        port=port,
        address="203.0.113.7",
    )
    agent = ua.UniversalAgent(uuid=owner, node=owner_node, name="host")
    target = ua.TargetResource(uuid=port, kind="evpn_port", agent=owner, hash="current")
    actual = ua.Resource(
        uuid=port,
        kind="evpn_port",
        node=owner_node,
        hash="current",
        status="ACTIVE",
        value={"fips": [{"public": address.address}]},
    )
    agents = {owner: agent}
    if condition == "wrong_owner":
        actual.node = uuid.uuid4()
    elif condition == "missing_agent":
        agents.clear()
    elif condition == "wrong_hash":
        actual.hash = "stale"
    elif condition == "wrong_ip":
        actual.value = {"fips": [{"public": "203.0.113.8"}]}
    elif condition == "error":
        actual.status = "ERROR"

    def forbidden(*args, **kwargs):
        raise AssertionError("status projection attempted an implicit SQL read")

    monkeypatch.setattr(engines.engine_factory, "get_engine", forbidden)
    assert address_status(address, target, actual, agents) == expected


def test_restored_related_port_cast_does_not_load_observations(monkeypatch):
    from exordos_core.network.dm import models as related

    project = uuid.uuid4()
    network = compute.Network(project_id=project)
    subnet = related.Subnet(
        project_id=project,
        network=network,
        cidr=compute.netaddr.IPNetwork("10.0.0.0/24"),
    )
    port = related.Port.restore(project_id=project, subnet=subnet)

    def forbidden(*args, **kwargs):
        raise AssertionError("casting attempted an implicit SQL read")

    monkeypatch.setattr(engines.engine_factory, "get_engine", forbidden)
    base = port.cast_to_base()
    assert base.subnet == subnet.uuid
    assert base.network is None
    assert base.security_groups == []
