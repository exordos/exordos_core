# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License").

from types import SimpleNamespace
from unittest import mock
import uuid

import pytest

from gcl_sdk.cloud.drivers.openstack import OpenStackDriver

from exordos_core.compute.dm import models
from exordos_core.network import routed
from exordos_core.network.driver.cloud import CloudNetworkDriver
from exordos_core.user_api.network.dm import models as catalog


class ProviderObject(dict):
    def __getattr__(self, name):
        return self[name]


class Neutron:
    def __init__(self):
        self.groups = []
        self.rules = []
        self.sequence = 0

    def security_groups(self):
        return self.groups

    def create_security_group(self, **values):
        self.sequence += 1
        group = ProviderObject(id=f"sg-{self.sequence}", **values)
        self.groups.append(group)
        return group

    def security_group_rules(self, security_group_id):
        return [r for r in self.rules if r.security_group_id == security_group_id]

    def create_security_group_rule(self, **values):
        self.sequence += 1
        rule = ProviderObject(id=f"rule-{self.sequence}", **values)
        self.rules.append(rule)
        return rule

    def delete_security_group_rule(self, identifier):
        self.rules = [r for r in self.rules if r.id != identifier]


@pytest.fixture
def topology(monkeypatch):
    provider = OpenStackDriver.__new__(OpenStackDriver)
    provider.config = SimpleNamespace(connection=uuid.uuid4())
    provider.api = SimpleNamespace(network=Neutron())
    group = SimpleNamespace(uuid=uuid.uuid4())
    roots = [SimpleNamespace(uuid=uuid.uuid4()) for _ in range(2)]
    segments = [
        SimpleNamespace(subnet=uuid.uuid4(), network=root.uuid) for root in roots
    ]
    ports = [
        SimpleNamespace(uuid=uuid.uuid4(), subnet=segment.subnet, ipv4=f"10.{i}.0.10")
        for i, segment in enumerate(segments, 1)
    ]
    members = [
        SimpleNamespace(port=port.uuid, security_group=group.uuid) for port in ports
    ]
    source = {
        "direction": "ingress",
        "protocol": "tcp",
        "port": 5432,
        "remote_group_id": str(group.uuid),
    }
    for cls in (
        catalog.PortSecurityGroup,
        catalog.SecurityGroup,
        catalog.SecurityGroupRule,
        models.Network,
        models.Port,
    ):
        monkeypatch.setattr(cls, "objects", mock.Mock())
    catalog.PortSecurityGroup.objects.get_all.side_effect = lambda filters: (
        [m for m in members if m.port == filters["port"].value]
        if "port" in filters
        else members
    )
    catalog.SecurityGroup.objects.get_one.return_value = group
    catalog.SecurityGroupRule.objects.get_all.return_value = [
        SimpleNamespace(dump_to_simple_view=lambda: source)
    ]
    models.Network.objects.get_one.side_effect = lambda filters: next(
        root for root in roots if root.uuid == filters["uuid"].value
    )
    models.Port.objects.get_all.return_value = ports
    monkeypatch.setattr(
        routed,
        "owner",
        lambda *, subnet: next(
            segment for segment in segments if segment.subnet == subnet
        ),
    )
    monkeypatch.setattr(
        routed,
        "parts",
        lambda root: [segment for segment in segments if segment.network == root.uuid],
    )
    drivers = []
    for root in roots:
        driver = CloudNetworkDriver.__new__(CloudNetworkDriver)
        driver.network = SimpleNamespace(
            uuid=uuid.uuid4(), owner_network=root.uuid, driver_state={}
        )
        driver.cloud = provider
        drivers.append(driver)
    return drivers, ports, provider.api.network


def test_shared_catalog_group_keeps_both_networks_rules_after_repeated_reconcile(
    topology,
):
    drivers, ports, neutron = topology
    first = drivers[0]._security_groups(ports[0])
    second = drivers[1]._security_groups(ports[1])
    assert first != second
    for _ in range(3):
        assert drivers[0]._security_groups(ports[0]) == first
        assert drivers[1]._security_groups(ports[1]) == second
        assert [r.remote_ip_prefix for r in neutron.security_group_rules(first[0])] == [
            "10.1.0.10/32"
        ]
        assert [
            r.remote_ip_prefix for r in neutron.security_group_rules(second[0])
        ] == ["10.2.0.10/32"]
    assert len(neutron.groups) == 2
    assert len(neutron.rules) == 2


def test_remote_group_reference_uses_same_network_scope(topology):
    drivers, ports, neutron = topology
    # Legacy cloud rules retain provider remote_group_id instead of CIDR expansion.
    for driver in drivers:
        driver.network.owner_network = None
    groups = [driver._security_groups(port)[0] for driver, port in zip(drivers, ports)]
    assert groups[0] != groups[1]
    for group in groups:
        assert neutron.security_group_rules(group)[0].remote_group_id == group


def test_cloud_segments_of_one_logical_network_share_provider_group(topology):
    drivers, ports, neutron = topology
    drivers[1].network.owner_network = drivers[0].network.owner_network
    assert drivers[0]._security_groups(ports[0]) == drivers[1]._security_groups(
        ports[0]
    )
    assert len(neutron.groups) == 1
