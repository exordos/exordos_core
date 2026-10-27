# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0

from concurrent.futures import ThreadPoolExecutor
import uuid

from bazooka import exceptions
import pytest
from restalchemy.dm import filters

from exordos_core.compute.dm import models as compute
from exordos_core.network.driver import evpn
from exordos_core.tests.functional.restapi.network.test_sdn_contract_api import create
from exordos_core.tests.functional.restapi.network.test_sdn_contract_api import resource
from exordos_core.user_api.network.dm import models as catalog


@pytest.fixture
def client(user_api_client, auth_user_admin, user_api):
    yield user_api_client(auth_user_admin)
    user_api._restore_seed_snapshot()


def group(client, **kwargs):
    return create(client, ("network", "catalog", "security_groups"), **kwargs)


def rule(client, target, source, **kwargs):
    return create(
        client,
        ("network", "catalog", "security_group_rules"),
        security_group=target["uuid"],
        remote_group_id=source["uuid"],
        direction="ingress",
        protocol="tcp",
        port=443,
        **kwargs,
    )


def port(client):
    return create(client, ("network", "ports"))


def bind(client, port, group):
    return create(
        client,
        ("network", "port_security_groups"),
        port=port["uuid"],
        security_group=group["uuid"],
    )


def compile_port(port):
    stored = compute.Port.objects.get_one(filters={"uuid": filters.EQ(port["uuid"])})
    subnet = compute.Subnet.objects.get_one(filters={"uuid": filters.EQ(stored.subnet)})
    driver = object.__new__(evpn.OvsEvpnNetworkDriver)
    return driver._compile_identity(
        stored, subnet, seed=False
    ), driver._compile_security_rules(stored, subnet, seed=False)


def test_self_reference_and_membership_changes(client):
    sg, first, second = group(client), port(client), port(client)
    first_link = bind(client, first, sg)
    bind(client, second, sg)
    assert compile_port(first)[0] == 0
    declaration = rule(client, sg, sg)
    assert declaration["remote_group_id"] == sg["uuid"]
    mark, rules = compile_port(first)
    assert mark and rules == [
        {"direction": "ingress", "proto": "tcp", "port": 443, "remote_identity": mark}
    ]
    assert compile_port(second) == (mark, rules)
    with pytest.raises(exceptions.ConflictError):
        client.delete(
            resource(client, "network", "catalog", "security_groups", sg["uuid"])
        )
    client.delete(
        resource(client, "network", "port_security_groups", first_link["uuid"])
    )
    assert compile_port(first) == (0, [])
    assert compile_port(second) == (mark, rules)


def test_same_sg_has_independent_stable_bits_per_network(client):
    first = port(client)
    other_network = create(
        client, ("network", "networks"), driver_spec={"driver": "ovs_evpn"}
    )
    other_subnet = create(
        client,
        ("network", "subnets"),
        network=other_network["uuid"],
        cidr="10.128.0.0/20",
    )
    second = create(client, ("network", "ports"), subnet=other_subnet["uuid"])
    sg, filler = group(client), group(client)
    catalog.SecurityGroupIdentity.ensure(
        uuid.UUID(other_network["uuid"]), uuid.UUID(filler["uuid"])
    )
    rule(client, sg, sg)
    bind(client, first, sg)
    bind(client, second, sg)
    first_mark, first_rules = compile_port(first)
    second_mark, second_rules = compile_port(second)
    assert first_mark == 1 and second_mark == 2
    assert first_rules[0]["remote_identity"] == first_mark
    assert second_rules[0]["remote_identity"] == second_mark
    assert compile_port(first) == (first_mark, first_rules)
    client.delete(
        resource(client, "network", "catalog", "security_groups", filler["uuid"])
    )
    assert compile_port(second) == (second_mark, second_rules)
    assert "network" not in sg


def test_remote_rule_added_after_source_port_changes_its_compilation(client):
    source, target = group(client), group(client)
    sender, receiver = port(client), port(client)
    bind(client, sender, source)
    bind(client, receiver, target)
    assert compile_port(sender)[0] == 0
    rule(client, target, source)
    mark, _ = compile_port(sender)
    assert mark and compile_port(receiver)[1][0]["remote_identity"] == mark
    unrelated = port(client)
    assert compile_port(unrelated)[0] == 0


def test_plain_security_groups_do_not_consume_identity_bits(client):
    guest = port(client)
    for _ in range(17):
        bind(client, guest, group(client))
    assert compile_port(guest)[0] == 0
    assert catalog.SecurityGroupIdentity.objects.get_all() == []


@pytest.mark.parametrize("rule_first", [False, True])
def test_bit_exhaustion_rejects_rule_or_binding_atomically(client, rule_first):
    guest, policy = port(client), group(client)
    bind(client, guest, policy)
    for _ in range(16):
        rule(client, policy, group(client))
    before = compile_port(guest)
    next_policy, next_source = group(client), group(client)
    if rule_first:
        rule(client, next_policy, next_source)
        with pytest.raises(exceptions.BadRequestError):
            bind(client, guest, next_policy)
        stored = compute.Port.objects.get_one(
            filters={"uuid": filters.EQ(guest["uuid"])}
        )
        compute.Port.project_observations([stored])
        assert uuid.UUID(next_policy["uuid"]) not in stored.security_groups
    else:
        with pytest.raises(exceptions.BadRequestError):
            rule(client, policy, next_source)
    assert compile_port(guest) == before
    assert len(catalog.SecurityGroupIdentity.objects.get_all()) == 16


def test_reference_validation_and_delete_guard(client):
    own = group(client)
    foreign = group(client, project_id=str(uuid.uuid4()))
    with pytest.raises(exceptions.BadRequestError):
        rule(client, own, foreign)
    with pytest.raises(exceptions.BadRequestError):
        rule(client, own, {"uuid": str(uuid.uuid4())})
    source = group(client)
    declaration = rule(client, own, source)
    url = resource(client, "network", "catalog", "security_groups", source["uuid"])
    with pytest.raises(exceptions.ConflictError):
        client.delete(url)
    client.delete(
        resource(
            client, "network", "catalog", "security_group_rules", declaration["uuid"]
        )
    )
    client.delete(url)


def test_parallel_assignments_are_unique_and_idempotent(client):
    guest = port(client)
    groups = [group(client) for _ in range(4)]

    def allocate(source):
        return catalog.SecurityGroupIdentity.ensure(
            uuid.UUID(guest["network"]), uuid.UUID(source["uuid"])
        ).identity

    with ThreadPoolExecutor(max_workers=4) as workers:
        identities = list(workers.map(allocate, groups + groups))
    assert len(set(identities)) == 4
    assert identities[:4] == identities[4:]


def test_concurrent_mutual_references_do_not_deadlock(client, monkeypatch):
    import threading

    first, second = group(client), group(client)
    guest = port(client)
    bind(client, guest, first)
    bind(client, guest, second)
    barrier = threading.Barrier(2)
    original = catalog.SecurityGroupIdentity.ensure

    def synchronized(network, security_group, session=None):
        barrier.wait(timeout=5)
        return original(network, security_group, session=session)

    monkeypatch.setattr(catalog.SecurityGroupIdentity, "ensure", synchronized)

    def insert(pair):
        target, source = pair
        catalog.SecurityGroupRule(
            project_id=uuid.UUID(int=0),
            security_group=uuid.UUID(target["uuid"]),
            direction="ingress",
            remote_group_id=uuid.UUID(source["uuid"]),
        ).insert()

    with ThreadPoolExecutor(max_workers=2) as workers:
        list(workers.map(insert, [(first, second), (second, first)]))
    assert len(catalog.SecurityGroupIdentity.objects.get_all()) == 2
