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

from exordos_core.cloud import links
from exordos_core.network.service import NetworkService


def test_neutron_port_binding_is_saved_with_its_observed_addresses():
    port_id = uuid.uuid4()
    desired = SimpleNamespace(
        uuid=port_id,
        node=uuid.uuid4(),
        ipv4="10.0.0.10",
        mask="255.255.255.0",
        source=None,
        mac="52:54:00:00:00:01",
        status="ACTIVE",
        update=mock.Mock(),
    )
    desired.cast_to_base = lambda: desired
    actual = SimpleNamespace(
        uuid=port_id,
        ipv4=desired.ipv4,
        mask=desired.mask,
        source=str(uuid.uuid4()),
        mac="fa:16:3e:00:00:01",
        status="ACTIVE",
    )
    subnet = SimpleNamespace(uuid=uuid.uuid4(), cidr="10.0.0.0/24", next_server=None)
    subnet.cast_to_base = lambda: subnet
    driver = mock.Mock()
    driver.list_ports.return_value = [actual]
    driver.compare_ports.return_value = contextlib.nullcontext()
    driver.port_is_stale.return_value = False
    NetworkService()._actualize_subnet(driver, subnet, subnet, [desired])
    assert desired.source == actual.source
    assert desired.mac == actual.mac
    desired.update.assert_called_once()


def test_cloud_reconcile_does_not_recreate_a_deleted_core_network():
    from restalchemy.storage import exceptions

    from exordos_core.network.driver.cloud import CloudNetworkDriver

    driver = CloudNetworkDriver.__new__(CloudNetworkDriver)
    driver.network = SimpleNamespace(uuid=uuid.uuid4())
    driver.cloud = mock.Mock()
    driver._network = mock.Mock()
    with (
        mock.patch(
            "exordos_core.network.driver.cloud.provider_lock",
            return_value=contextlib.nullcontext(),
        ),
        mock.patch(
            "exordos_core.network.driver.cloud.models.Network.objects"
        ) as networks,
    ):
        networks.get_one.side_effect = exceptions.RecordNotFound(
            model="Network", filters={}
        )
        import pytest

        with pytest.raises(exceptions.RecordNotFound):
            with driver.reconcile():
                pytest.fail("A deleted cloud network must not reconcile")
    driver._network.assert_not_called()


def test_link_cleanup_preserves_routes_owned_by_another_gateway():
    endpoint = SimpleNamespace(
        uuid=uuid.uuid4(), connection=uuid.uuid4(), delete=mock.Mock()
    )
    link = SimpleNamespace(uuid=uuid.uuid4())
    port = SimpleNamespace(
        id=str(uuid.uuid4()), fixed_ips=[{"ip_address": "10.244.50.15"}]
    )
    subnet = SimpleNamespace(
        id=str(uuid.uuid4()),
        host_routes=[
            {"destination": "192.168.244.0/24", "nexthop": "10.244.50.15"},
            {"destination": "172.20.0.0/24", "nexthop": "10.244.50.20"},
        ],
    )
    cloud = mock.Mock()
    cloud.server.return_value = None
    cloud._find.side_effect = lambda objects, kind, identity: (
        port if kind == "port" else None
    )
    cloud.api.network.ips.return_value = []
    service = links.CloudLinkService()
    with (
        mock.patch.object(
            service, "_provider", return_value=(cloud, mock.Mock(), subnet)
        ),
        mock.patch.object(links.ua.TargetResource, "objects") as targets,
        mock.patch.object(links.ua.Resource, "objects") as actual,
        mock.patch.object(links.catalog.Address, "objects") as addresses,
        mock.patch.object(links.ua.UniversalAgent, "objects") as agents,
        mock.patch.object(links.ua.NodeEncryptionKey, "objects") as keys,
    ):
        for obj in [targets, actual, addresses, agents, keys]:
            obj.get_all.return_value = []
        assert service._remove(link, endpoint)
    cloud.api.network.update_subnet.assert_called_once_with(
        subnet.id, host_routes=[subnet.host_routes[1]]
    )
    endpoint.delete.assert_called_once()


@pytest.mark.parametrize("destination", ["10.1.0.0/16", "10.1.5.0/24", "10.0.0.0/8"])
def test_link_rejects_existing_routes_overlapping_peer_prefix(destination):
    service = links.CloudLinkService()
    cloud = mock.Mock()
    subnet = SimpleNamespace(
        id="subnet",
        host_routes=[{"destination": destination, "nexthop": "192.168.1.20"}],
    )
    with mock.patch.object(service, "_provider", return_value=(cloud, None, subnet)):
        with pytest.raises(ValueError, match="conflicts"):
            service._routes(
                SimpleNamespace(connection=uuid.uuid4()),
                {"gateway_ip": "192.168.1.10"},
                ["10.1.0.0/16"],
            )
    cloud.api.network.update_subnet.assert_not_called()


def test_link_gateway_ports_are_excluded_from_workload_port_reconciliation():
    from exordos_core.network.driver.cloud import CloudNetworkDriver

    driver = CloudNetworkDriver.__new__(CloudNetworkDriver)
    driver.cloud = mock.Mock()
    gateway = SimpleNamespace(uuid=uuid.uuid4())
    workload = SimpleNamespace(uuid=uuid.uuid4())
    driver.cloud.ports.return_value = [gateway, workload]
    driver.cloud.identity.side_effect = lambda port, kind: port.uuid
    driver._network = mock.Mock()
    driver._subnet = mock.Mock()
    driver._port_model = lambda port: port
    with mock.patch(
        "exordos_core.network.driver.cloud.endpoints_for_subnet", return_value=[gateway]
    ):
        assert driver.list_ports(SimpleNamespace(uuid=uuid.uuid4())) == [workload]


@pytest.mark.parametrize("explicit", [False, True])
def test_link_material_issues_tls_keys_for_the_selected_server(explicit):
    first = SimpleNamespace(
        uuid=uuid.UUID(int=1), definition={"role": "client"} if explicit else {}
    )
    second = SimpleNamespace(
        uuid=uuid.UUID(int=2), definition={"role": "server"} if explicit else {}
    )
    endpoints = [first, second]
    link = SimpleNamespace(uuid=uuid.uuid4(), secrets={}, observed=mock.Mock())
    service = links.CloudLinkService()
    with (
        mock.patch.object(links.CloudNetworkLink, "objects") as stored,
        mock.patch.object(service, "_bindings", return_value=endpoints),
        mock.patch.object(
            service,
            "_subnet",
            side_effect=lambda e: SimpleNamespace(cidr=f"10.{e.uuid.int}.0.0/24"),
        ),
        mock.patch.object(
            links.openvpn,
            "tls_key",
            side_effect=lambda key=None: "client-key" if key else "server-key",
        ),
    ):
        stored.get_all.return_value = [link]
        material = service._material(link, endpoints)["endpoints"]
    client, server = (first, second) if explicit else (second, first)
    assert material[str(client.uuid)]["tls_clients"] == {server.uuid.hex: "client-key"}
    assert material[str(server.uuid)]["tls_clients"] == {}


def test_cloud_authentication_error_does_not_echo_provider_secrets():
    from exordos_core.cloud.specs import OpenStackConnectionSpec
    from exordos_core.common.exceptions import ValidateException

    config = OpenStackConnectionSpec(auth_url="https://identity.example/v3", auth={})
    with mock.patch.object(
        config, "load_driver", side_effect=RuntimeError("private-sentinel")
    ):
        with pytest.raises(ValidateException) as error:
            config.authenticate(uuid.uuid4())
    assert "private-sentinel" not in str(error.value)


@pytest.mark.parametrize(
    "boot,ports,expected",
    [
        (
            "http://core.example:11013",
            (11011, 11012),
            ("http://core.example:11011", "http://core.example:11012"),
        ),
        (
            "https://[2001:db8::1]:443/boot/",
            (11443, 12443),
            ("http://[2001:db8::1]:11443", "http://[2001:db8::1]:12443"),
        ),
    ],
)
def test_cloud_bootstrap_uses_the_installation_runtime_endpoints_and_sdk(
    boot, ports, expected
):
    from exordos_core.cloud import bootstrap

    with mock.patch.object(bootstrap, "version", return_value="4.5.6"):
        bootstrap.CONF.set_override("gc_boot_api", boot, group="boot_api")
        bootstrap.CONF.set_override("bind_port", ports[0], group="orch_api")
        bootstrap.CONF.set_override("bind_port", ports[1], group="status_api")
        try:
            assert bootstrap.agent_config() == (*expected, "gcl_sdk==4.5.6")
        finally:
            bootstrap.CONF.clear_override("gc_boot_api", group="boot_api")
            bootstrap.CONF.clear_override("bind_port", group="orch_api")
            bootstrap.CONF.clear_override("bind_port", group="status_api")


def test_cloud_controller_resolves_credentials_without_serializing_them(monkeypatch):
    from gcl_sdk.agents.universal.drivers.pool import CloudPoolDriverSpec

    from exordos_core.cloud import agent

    monkeypatch.setattr(agent.ControllerMetaPool, "__driver_map__", {})
    spec = mock.Mock()
    spec.dump_to_simple_view.return_value = {"credential": "first"}
    connection = SimpleNamespace(driver_spec=spec, load_driver=mock.Mock())
    pool = agent.ControllerMetaPool(
        uuid=uuid.uuid4(), driver_spec=CloudPoolDriverSpec(connection=uuid.uuid4())
    )
    with mock.patch.object(agent, "connection", return_value=connection):
        first = pool.load_driver()
        assert first is pool.load_driver()
        assert first.cloud is connection.load_driver.return_value
        assert set(pool.driver_spec.dump_to_simple_view()) == {
            "kind",
            "connection",
            "volume_type",
            "key_name",
        }
        spec.dump_to_simple_view.return_value = {"credential": "second"}
        assert pool.load_driver() is not first
        second = pool.load_driver()
        pool.driver_spec.volume_type = "durable"
        assert pool.load_driver() is not second
    assert connection.load_driver.call_count == 3


def test_cloud_connection_logging_omits_bodies_and_queries(caplog):
    import logging

    import webob

    from exordos_core.cloud.api import HypervisorCredentialsLoggingMiddleware

    request = webob.Request.blank(
        "/v1/compute/hypervisors/?auth=private-sentinel",
        method="POST",
        json_body={"auth": {"password": "private-sentinel"}},
    )
    app = HypervisorCredentialsLoggingMiddleware(
        lambda env, start: webob.Response(json_body={"ok": True})(env, start)
    )
    with caplog.at_level(logging.DEBUG, logger="restalchemy.api.middlewares.logging"):
        response = request.get_response(app)
    assert response.status_int == 200
    assert "private-sentinel" not in caplog.text
    assert "hypervisors" in caplog.text
