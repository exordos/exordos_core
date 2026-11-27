# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0.
"""Observed network fields are loaded explicitly, once per collection."""

import json
import uuid

from gcl_sdk.agents.universal.clients.backend.db import DatabaseBackendClient, ModelSpec
from gcl_sdk.agents.universal.dm import models as ua
from gcl_sdk.agents.universal.storage.fs import TargetFieldsFileStorage
import pytest
from restalchemy.common import contexts
from restalchemy.dm import filters
from restalchemy.storage.sql import sessions

from exordos_core.compute.dm import models as compute
from exordos_core.tests.functional.restapi.network.test_default_network import (
    client as client,
    guest,
    pool,
)
from exordos_core.tests.functional.restapi.network.test_sdn_contract_api import (
    collection,
    create,
    node_ports,
    resource,
)
from exordos_core.user_api.network.dm import models as catalog


@pytest.fixture
def selects(monkeypatch):
    statements = []
    original = sessions.PgSQLSession.execute

    def execute(session, statement, values=None):
        if str(statement).lstrip().upper().startswith("SELECT"):
            statements.append(str(statement))
        return original(session, statement, values)

    monkeypatch.setattr(sessions.PgSQLSession, "execute", execute)
    return statements


@pytest.mark.parametrize("size", [1, 3])
def test_batch_views_preserve_outputs_and_use_constant_queries(client, selects, size):
    project = uuid.uuid4()
    public = pool(client)
    nodes, ports, addresses, groups = [], [], [], []
    for index in range(size):
        node = guest(client, project)
        port = node_ports(client, node)[0]
        address = create(
            client,
            ("network", "catalog", "addresses"),
            project_id=str(project),
            subnet=public["uuid"],
            kind="floating",
            port=port["uuid"],
        )
        group = create(
            client, ("network", "catalog", "security_groups"), project_id=str(project)
        )
        create(
            client,
            ("network", "port_security_groups"),
            project_id=str(project),
            port=port["uuid"],
            security_group=group["uuid"],
        )
        # Insert in reverse UUID order; the response and hash must be stable.
        for rule_id, tcp_port in [
            (uuid.UUID(int=20 + index * 2), 443),
            (uuid.UUID(int=19 + index * 2), 22),
        ]:
            create(
                client,
                ("network", "catalog", "security_group_rules"),
                uuid=str(rule_id),
                project_id=str(project),
                security_group=group["uuid"],
                direction="ingress",
                protocol="tcp",
                port=tcp_port,
            )
        owner = ua.UniversalAgent(
            uuid=uuid.uuid4(), name="host", node=uuid.UUID(node["uuid"])
        )
        owner.insert()
        target = ua.TargetResource(
            uuid=uuid.UUID(port["uuid"]),
            kind="evpn_port",
            agent=owner.uuid,
            value={"fips": [{"public": address["address"]}]},
        )
        target.calculate_hash()
        target.insert()
        ua.Resource(
            uuid=target.uuid,
            kind=target.kind,
            node=owner.node,
            value=target.value,
            hash=target.hash,
            status="ACTIVE",
        ).insert()
        nodes.append(node)
        ports.append(port)
        addresses.append(address)
        groups.append(group)

    for model, source, expected_reads in (
        (compute.Port, ports, 2),
        (catalog.SecurityGroup, groups, 1),
        (catalog.Address, addresses, 4),
        (compute.Node, nodes, 3),
    ):
        stored = model.objects.get_all(
            filters={"uuid": filters.In([r["uuid"] for r in source])}
        )
        prepared = [row._get_prepared_data() for row in stored]
        selects.clear()
        with contexts.Context().session_manager() as session:
            model.project_observations(iter(stored), session=session)
        assert len(selects) == expected_reads, selects
        selects.clear()
        views = [row.dump_to_simple_view() for row in stored]
        for row in stored:
            row.get_ua_all_and_target_values()
        assert selects == []
        assert [row._get_prepared_data() for row in stored] == prepared
        if model is compute.Port:
            assert all(
                view["network"] and len(view["security_groups"]) == 1 for view in views
            )
        elif model is catalog.SecurityGroup:
            assert all(
                [rule["port"] for rule in view["rules"]] == [22, 443] for view in views
            )
        elif model is catalog.Address:
            assert all(view["network"] and view["status"] == "ACTIVE" for view in views)
        else:
            for row in stored:
                assert row.default_network["floating_ip"]
                assert row.default_network["port"]
                assert json.loads(
                    row._get_prepared_data()["default_network"]
                ) == row.properties.get_value("default_network")
                row.name = "changed"
                row.update()
                assert row.default_network == row.properties.get_value(
                    "default_network"
                )


def test_db_backend_projects_crud_and_list_without_changing_requests(
    client, tmp_path, selects
):
    project = uuid.uuid4()
    network = create(
        client,
        ("network", "networks"),
        project_id=str(project),
        driver_spec={"driver": "ovs_evpn"},
    )
    create(
        client,
        ("network", "subnets"),
        project_id=str(project),
        network=network["uuid"],
        cidr="10.77.0.0/24",
    )
    storage = TargetFieldsFileStorage(str(tmp_path / "target-fields.json"))
    (spec,) = ModelSpec.from_collection(
        [(compute.Port, "port")], {"project_id": filters.EQ(str(project))}
    )
    backend = DatabaseBackendClient([spec], storage)
    for _ in range(3):
        value = {"uuid": str(uuid.uuid4()), "network": network["uuid"]}
        request = ua.Resource.from_value(value, "port", frozenset(value))
        created = backend.create(request)
        assert str(created.network) == network["uuid"]
        assert created._network_selector == uuid.UUID(network["uuid"])
        stored = backend.get(request)
        assert str(stored.network) == network["uuid"]
        assert getattr(stored, "_network_selector", None) is None
        update = ua.Resource.from_value(
            {
                "uuid": value["uuid"],
                "project_id": str(project),
                "description": "updated",
            },
            "port",
        )
        changed = backend.update(update)
        assert str(changed.network) == network["uuid"]
        assert changed.description == "updated"
    selects.clear()
    rows = backend.list("port")
    assert len(rows) == 3
    assert len(selects) == 3, selects
    selects.clear()
    assert all(
        row.to_ua_resource("port").value["network"] == network["uuid"] for row in rows
    )
    assert selects == []


def test_api_projects_after_pagination_and_keeps_field_permissions(client):
    project = uuid.uuid4()
    group = create(
        client, ("network", "catalog", "security_groups"), project_id=str(project)
    )
    create(
        client,
        ("network", "catalog", "security_group_rules"),
        project_id=str(project),
        security_group=group["uuid"],
        direction="egress",
        protocol="icmp",
    )
    path = collection(client, "network", "catalog", "security_groups")
    response = client.get(path, params={"project_id": str(project), "page_limit": 1})
    assert response.json()[0]["rules"] == [{"direction": "egress", "protocol": "icmp"}]
    updated = client.put(
        resource(client, "network", "catalog", "security_groups", group["uuid"]),
        json={"description": "changed"},
    )
    assert updated.json()["rules"] == response.json()[0]["rules"]
    shown = client.get(
        path, params={"project_id": str(project), "fields": "uuid"}
    ).json()
    assert shown == [{"uuid": group["uuid"]}]
