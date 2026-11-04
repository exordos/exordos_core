# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0

import contextlib
from types import SimpleNamespace
from unittest import mock
import uuid

import pytest

from exordos_core.network import routed
from exordos_core.tests.unit.network.test_cloud_security_groups import (
    topology as topology,
)


def test_native_logical_segment_does_not_request_neutron_router():
    network = SimpleNamespace(
        owner_network=uuid.uuid4(), driver_spec={"driver": "ovs_evpn"}
    )
    with mock.patch.object(routed, "owner") as owner:
        assert routed.router_id(network) is None
    owner.assert_not_called()


@pytest.mark.parametrize("port_security", [True, False])
def test_remote_security_groups_do_not_depend_on_anti_spoofing(port_security):
    from exordos_core.network.driver.core import CoreNetworkDriver

    driver = object.__new__(CoreNetworkDriver)
    driver.cloud = mock.Mock(project=str(uuid.uuid4()))
    driver.cloud.path.side_effect = lambda kind: kind
    groups = [str(uuid.uuid4()), str(uuid.uuid4())]
    driver._security_groups = mock.Mock(return_value=groups)
    port = SimpleNamespace(uuid=uuid.uuid4(), port_security=port_security)
    obsolete = str(uuid.uuid4())
    driver.cloud.client.filter.return_value = [
        {"uuid": str(uuid.uuid4()), "security_group": groups[0]},
        {"uuid": obsolete, "security_group": str(uuid.uuid4())},
    ]
    driver._bindings(port, {"uuid": str(uuid.uuid4())})
    assert {
        call.kwargs["security_group"] for call in driver.cloud.ensure.call_args_list
    } == set(groups)
    driver.cloud.client.delete.assert_called_once_with(
        "port_security_group", uuid.UUID(obsolete)
    )


@pytest.mark.parametrize("port_security", [True, False])
def test_missing_remote_group_is_stale_with_either_anti_spoof_setting(port_security):
    from exordos_core.network.driver.core import CoreNetworkDriver

    driver = object.__new__(CoreNetworkDriver)
    driver.cloud = mock.Mock()
    group = str(uuid.uuid4())
    driver._security_groups = mock.Mock(return_value=[group])
    port = SimpleNamespace(
        uuid=uuid.uuid4(), port_security=port_security, enforce_security_group=False
    )
    remote = {
        "security_groups": [group],
        "port_security": port_security,
        "enforce_security_group": False,
    }
    driver.cloud.get.return_value = remote
    assert driver.port_is_stale(port) is False
    remote["security_groups"] = []
    assert driver.port_is_stale(port) is True


def test_remote_core_group_reconciliation_keeps_other_network_rules(topology):
    from exordos_core.network.driver.core import CoreNetworkDriver

    previous, ports, _ = topology
    data = {"security_group": {}, "security_group_rule": {}}
    cloud = mock.Mock(project=str(uuid.uuid4()))

    def ensure(kind, identity, **fields):
        return data[kind].setdefault(str(identity), {"uuid": str(identity), **fields})

    cloud.ensure.side_effect = ensure
    cloud.resource_id.side_effect = lambda kind, identity: identity
    cloud.path.side_effect = lambda kind: kind
    cloud.client.filter.side_effect = lambda kind, **fields: [
        v
        for v in data[kind].values()
        if v["security_group"] == fields["security_group"]
    ]

    def delete(kind, identity):
        del data[kind][str(identity)]

    cloud.client.delete.side_effect = delete
    drivers = []
    for original in previous:
        driver = object.__new__(CoreNetworkDriver)
        driver.network, driver.cloud = original.network, cloud
        drivers.append(driver)
    first = drivers[0]._security_groups(ports[0])
    second = drivers[1]._security_groups(ports[1])
    assert first != second
    for _ in range(3):
        assert drivers[0]._security_groups(ports[0]) == first
        assert drivers[1]._security_groups(ports[1]) == second
        assert {
            v["remote_ip"]
            for v in data["security_group_rule"].values()
            if v["security_group"] in first
        } == {"10.1.0.10/32"}
        assert {
            v["remote_ip"]
            for v in data["security_group_rule"].values()
            if v["security_group"] in second
        } == {"10.2.0.10/32"}
    assert len(data["security_group_rule"]) == 2


def test_link_cleanup_does_not_update_a_retiring_local_subnet(monkeypatch):
    from exordos_core.cloud.links import CloudLinkService
    from exordos_core.network import subnet as subnet_module

    service = object.__new__(CloudLinkService)
    endpoint = SimpleNamespace(
        connection=None, definition={"external_subnet": str(uuid.uuid4())}
    )
    subnet = SimpleNamespace(status="DELETING", update=mock.Mock())
    service._subnet = lambda endpoint: subnet
    monkeypatch.setattr(
        subnet_module,
        "effective_routers",
        lambda subnet: [{"to": "10.20.0.0/24", "via": "10.10.0.2"}],
    )
    service._routes(endpoint, {"gateway_ip": "10.10.0.2"}, [])
    subnet.update.assert_not_called()


def test_link_cleanup_does_not_update_a_retiring_remote_subnet():
    from exordos_core.cloud import core_links

    cloud = mock.Mock()
    cloud.get.return_value = {
        "status": "DELETING",
        "routers": [{"to": "10.20.0.0/24", "via": "10.10.0.2"}],
    }
    core_links.routes(
        cloud, SimpleNamespace(subnet=uuid.uuid4()), {"gateway_ip": "10.10.0.2"}, []
    )
    cloud.update.assert_not_called()


@pytest.mark.parametrize(
    "status,retiring", [("ACTIVE", False), ("DELETING", True), (None, True)]
)
def test_remote_network_retirement_follows_owning_segment(
    monkeypatch, status, retiring
):
    from exordos_core.network.driver.core import CoreNetworkDriver

    driver = object.__new__(CoreNetworkDriver)
    driver.network = SimpleNamespace(uuid=uuid.uuid4(), owner_network=uuid.uuid4())
    monkeypatch.setattr(
        routed,
        "owner",
        lambda **kwargs: SimpleNamespace(status=status) if status else None,
    )
    assert driver._retiring() is retiring


def test_remote_reconciliation_does_not_recreate_retiring_network(monkeypatch):
    from exordos_core.network.driver import core

    driver = object.__new__(core.CoreNetworkDriver)
    driver.cloud = mock.Mock()
    driver.network = SimpleNamespace(uuid=uuid.uuid4())
    driver.bind = mock.Mock()
    driver._retiring = lambda: True
    driver._network = mock.Mock()
    monkeypatch.setattr(
        core, "provider_lock", lambda connection: contextlib.nullcontext()
    )
    monkeypatch.setattr(core.models.Network, "objects", mock.Mock())
    with driver.reconcile():
        pass
    driver._network.assert_not_called()


def test_remote_reconciliation_does_not_recreate_retiring_subnet():
    from exordos_core.network.driver.core import CoreNetworkDriver

    driver = object.__new__(CoreNetworkDriver)
    driver.cloud = mock.Mock()
    subnet = SimpleNamespace(status="DELETING")
    assert driver.create_subnet(subnet) is subnet
    assert driver.update_subnet(subnet) is subnet
    driver.cloud.ensure.assert_not_called()
    driver.cloud.update.assert_not_called()
