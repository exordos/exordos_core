#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

import uuid

from bazooka import exceptions
import netaddr
import pytest


def collection(client, *path):
    return client.build_collection_uri(list(path))


def resource(client, *path):
    return client.build_resource_uri(list(path))


def create(client, path, **body):
    body.setdefault("project_id", "00000000-0000-0000-0000-000000000000")
    response = client.post(collection(client, *path), json=body)
    assert response.status_code == 201, response.text
    return response.json()


def node_ports(client, node):
    uuid = node["uuid"] if isinstance(node, dict) else str(node.uuid)
    ports = client.get(
        collection(client, "network", "ports"), params={"node": uuid}
    ).json()
    return sorted(ports, key=lambda port: port["index"])


@pytest.fixture
def topology(user_api_client, auth_user_admin):
    client = user_api_client(auth_user_admin)
    network = create(
        client,
        ("network", "networks"),
        name="overlay",
        driver_spec={"driver": "ovs_evpn"},
    )
    subnet = create(
        client,
        ("network", "subnets"),
        name="private",
        network=network["uuid"],
        cidr="10.101.0.0/24",
    )
    return client, network, subnet


@pytest.mark.parametrize("enforce", [False, True])
def test_port_security_group_enforcement_survives_binding_changes(topology, enforce):
    from restalchemy.dm import filters

    from exordos_core.compute.dm import models as compute
    from exordos_core.network.driver import evpn

    client, network, _ = topology
    port = create(
        client,
        ("network", "ports"),
        network=network["uuid"],
        **({"enforce_security_group": True} if enforce else {}),
    )
    assert port["enforce_security_group"] is enforce
    assert port["port_security"] is True
    driver = object.__new__(evpn.OvsEvpnNetworkDriver)

    def compiled():
        stored = compute.Port.objects.get_one(
            filters={"uuid": filters.EQ(port["uuid"])}
        )
        return driver._compile_security_rules(stored)

    unbound = list(driver.DENY_ALL) if enforce else []
    assert compiled() == unbound
    group = create(client, ("network", "catalog", "security_groups"))
    binding = create(
        client,
        ("network", "port_security_groups"),
        port=port["uuid"],
        security_group=group["uuid"],
    )
    assert compiled() == list(driver.DENY_ALL)
    for direction, protocol, fields in (
        ("ingress", "tcp", {"port": 22}),
        ("egress", "icmp", {}),
    ):
        create(
            client,
            ("network", "catalog", "security_group_rules"),
            security_group=group["uuid"],
            direction=direction,
            protocol=protocol,
            **fields,
        )
    assert sorted(compiled(), key=lambda rule: rule["direction"]) == [
        {"direction": "egress", "proto": "icmp"},
        {"direction": "ingress", "proto": "tcp", "port": 22},
    ]
    client.delete(resource(client, "network", "port_security_groups", binding["uuid"]))
    assert compiled() == unbound
    url = resource(client, "network", "ports", port["uuid"])
    updated = client.put(url, json={"enforce_security_group": not enforce}).json()
    assert updated["enforce_security_group"] is not enforce
    assert updated["port_security"] is True
    assert compiled() == ([] if enforce else list(driver.DENY_ALL))


def test_flat_ports_reject_unsupported_security_group_enforcement(topology):
    from restalchemy.dm import filters

    from exordos_core.compute.dm import models as compute

    client, _, _ = topology
    network = compute.Network(
        project_id=uuid.UUID(int=0), driver_spec={"driver": "flat_bridge"}
    )
    network.insert()
    subnet = compute.Subnet(
        project_id=network.project_id,
        network=network.uuid,
        cidr=netaddr.IPNetwork("10.102.0.0/24"),
    )
    subnet.insert()
    with pytest.raises(exceptions.BadRequestError) as error:
        create(
            client,
            ("network", "ports"),
            subnet=str(subnet.uuid),
            enforce_security_group=True,
        )
    assert "enforce_security_group requires an overlay network" in (
        error.value.cause.response.text
    )
    assert not compute.Port.objects.get_all(filters={"subnet": filters.EQ(subnet.uuid)})
    port = create(client, ("network", "ports"), subnet=str(subnet.uuid))
    url = resource(client, "network", "ports", port["uuid"])
    with pytest.raises(exceptions.BadRequestError) as error:
        client.put(url, json={"enforce_security_group": True})
    assert "enforce_security_group requires an overlay network" in (
        error.value.cause.response.text
    )
    assert client.get(url).json()["enforce_security_group"] is False


def test_node_and_independent_port_bindings(topology):
    client, network, subnet = topology
    node = create(
        client,
        ("compute", "nodes"),
        name="guest",
        cores=1,
        ram=1024,
        disk_spec={"kind": "root_disk", "image": "ubuntu_24.04"},
        default_network={},
    )
    assert node_ports(client, node) == []
    port = create(
        client, ("network", "ports"), network=network["uuid"], node=node["uuid"]
    )
    assert port["index"] == 0
    assert port["ipv4"]
    assert port["security_groups"] == []
    group = create(client, ("network", "catalog", "security_groups"), name="ssh")
    rule = create(
        client,
        ("network", "catalog", "security_group_rules"),
        security_group=group["uuid"],
        direction="ingress",
        protocol="tcp",
        port=22,
    )
    link = create(
        client,
        ("network", "port_security_groups"),
        port=port["uuid"],
        security_group=group["uuid"],
    )
    url = resource(client, "network", "ports", port["uuid"])
    assert client.get(url).json()["security_groups"] == [group["uuid"]]
    assert (
        node_ports(
            client,
            client.get(resource(client, "compute", "nodes", node["uuid"])).json(),
        )[0]["uuid"]
        == port["uuid"]
    )
    assert (
        len(
            client.get(
                resource(client, "network", "catalog", "security_groups", group["uuid"])
            ).json()["rules"]
        )
        == 1
    )
    # Neither an unrelated update nor omitted index/memberships resets state.
    assert client.put(url, json={"name": "renamed"}).json()["index"] == 0
    assert client.put(url, json={"node": None}).json().get("index") is None
    assert client.put(url, json={"node": node["uuid"]}).json()["security_groups"] == [
        group["uuid"]
    ]
    with pytest.raises(exceptions.ConflictError):
        client.delete(url)
    client.delete(resource(client, "network", "port_security_groups", link["uuid"]))
    client.delete(
        resource(client, "network", "catalog", "security_group_rules", rule["uuid"])
    )
    client.delete(resource(client, "compute", "nodes", node["uuid"]))
    detached = client.get(url).json()
    assert detached.get("node") is None and detached.get("index") is None


def test_default_port_and_independent_secondary_port(topology):
    client, network, subnet = topology
    node = create(
        client,
        ("compute", "nodes"),
        name="two-nics",
        cores=1,
        ram=1024,
        disk_spec={"kind": "root_disk", "image": "ubuntu_24.04"},
        default_network={"network": network["uuid"]},
    )
    secondary = create(
        client, ("network", "ports"), subnet=subnet["uuid"], node=node["uuid"]
    )
    assert node_ports(client, node)[0]["index"] == 0 and secondary["index"] == 1
    current = client.get(resource(client, "compute", "nodes", node["uuid"])).json()
    assert len(node_ports(client, current)) == 2
    assert current["default_network"]["port"] == node_ports(client, node)[0]["uuid"]


def test_floating_address_can_select_a_network(topology):
    client, network, subnet = topology
    port = create(client, ("network", "ports"), network=network["uuid"])
    # External RFC1918 pool on a published network.
    external = create(
        client,
        ("network", "networks"),
        name="external",
        access="public",
        driver_spec={"driver": "ovs_evpn"},
    )
    pool = create(
        client,
        ("network", "subnets"),
        name="external-pool",
        network=external["uuid"],
        cidr="192.168.1.0/24",
        ip_range="192.168.1.50-192.168.1.99",
        dhcp=False,
        placeable=False,
        floating_pool=True,
    )
    fip = create(
        client,
        ("network", "catalog", "addresses"),
        kind="floating",
        network=external["uuid"],
        port=port["uuid"],
    )
    assert fip["subnet"] == pool["uuid"] and fip["address"] == "192.168.1.50"
    assert fip["status"] == "IN_PROGRESS"
    assert (
        "floating_ips"
        not in client.get(resource(client, "network", "ports", port["uuid"])).json()
    )
    assert [
        item["uuid"]
        for item in client.get(
            collection(client, "network", "catalog", "addresses"),
            params={"port": port["uuid"]},
        ).json()
    ] == [fip["uuid"]]
    fip_url = resource(client, "network", "catalog", "addresses", fip["uuid"])
    assert client.put(fip_url, json={"port": None}).json()["status"] == "UNBOUND"
    with pytest.raises(exceptions.ConflictError):
        client.put(fip_url, json={"kind": "reservation"})
    assert client.delete(fip_url).status_code == 204


@pytest.mark.parametrize("patch", [{"index": None}, {"index": True}, {"network": None}])
def test_invalid_optional_values_are_rejected(topology, patch):
    client, network, subnet = topology
    node = create(
        client,
        ("compute", "nodes"),
        name="guest",
        cores=1,
        ram=1024,
        disk_spec={"kind": "root_disk", "image": "ubuntu_24.04"},
        default_network={},
    )
    body = {"network": network["uuid"], "node": node["uuid"], **patch}
    with pytest.raises(exceptions.BadRequestError):
        client.post(collection(client, "network", "ports"), json=body)


def test_mac_uniqueness_spans_network_subnets(topology):
    client, network, subnet = topology
    other = create(
        client, ("network", "subnets"), network=network["uuid"], cidr="10.102.0.0/24"
    )
    mac = "52:54:00:ab:cd:ef"
    create(client, ("network", "ports"), subnet=subnet["uuid"], target_mac=mac)
    with pytest.raises(exceptions.ConflictError):
        create(
            client, ("network", "ports"), subnet=other["uuid"], target_mac=mac.upper()
        )


@pytest.mark.parametrize(
    "port",
    [
        {"network": "broken"},
        {"network": "00000000-0000-0000-0000-000000000000", "mac": "52:54:00:aa:bb:cc"},
        {
            "network": "00000000-0000-0000-0000-000000000000",
            "target_mac": "ff:ff:ff:ff:ff:ff",
        },
    ],
)
def test_invalid_inline_port_is_rejected(topology, port):
    client, network, subnet = topology
    with pytest.raises(exceptions.BadRequestError):
        create(
            client,
            ("compute", "nodes"),
            name="bad-inline",
            cores=1,
            ram=1024,
            disk_spec={"kind": "root_disk", "image": "ubuntu_24.04"},
            default_network=port,
        )


def test_subnet_defaults_preserve_omitted_and_distinguish_empty(topology):
    from restalchemy.dm import filters

    from exordos_core.compute.dm import models as compute
    from exordos_core.network.dhcp import isc

    client, network, subnet = topology
    url = resource(client, "network", "subnets", subnet["uuid"])

    def check(view, routers, dns, gateway, resolver):
        assert view.get("routers") == routers
        assert view.get("dns_servers") == dns
        assert not {"effective_routers", "effective_dns_servers"} & view.keys()
        stored = compute.Subnet.objects.get_one(
            filters={"uuid": filters.EQ(subnet["uuid"])}
        )
        assert stored.dump_to_simple_view().get("routers") == routers
        assert stored.dns_servers == dns
        config = isc.dhcp_config({stored: []})
        if gateway:
            assert f"option routers {gateway};" in config
        else:
            assert "option routers" not in config
        if resolver:
            assert f"option domain-name-servers {resolver};" in config
        else:
            assert "domain-name-servers" not in config

    check(subnet, None, None, "10.101.0.1", "10.101.0.1")
    check(client.get(url).json(), None, None, "10.101.0.1", "10.101.0.1")
    routes = [{"to": "0.0.0.0/0", "via": "10.101.0.9"}]
    for changes, routers, dns, gateway, resolver in (
        ({"name": "renamed"}, None, None, "10.101.0.1", "10.101.0.1"),
        ({"routers": [], "dns_servers": []}, [], [], None, None),
        ({"dhcp": False}, [], [], None, None),
        ({"routers": None, "dns_servers": None}, None, None, None, None),
        ({"dhcp": True}, None, None, "10.101.0.1", "10.101.0.1"),
        (
            {"routers": routes, "dns_servers": ["8.8.8.8"], "dhcp": False},
            routes,
            ["8.8.8.8"],
            "10.101.0.9",
            "8.8.8.8",
        ),
        ({"name": "preserved"}, routes, ["8.8.8.8"], "10.101.0.9", "8.8.8.8"),
    ):
        check(client.put(url, json=changes).json(), routers, dns, gateway, resolver)
    assert all(
        not {"effective_routers", "effective_dns_servers"} & view.keys()
        for view in client.get(collection(client, "network", "subnets")).json()
    )
    assert client.put(url, json={"network": network["uuid"]}).status_code == 200


def test_network_settings_replace_without_overwriting_allocated_state(topology):
    client, network, subnet = topology
    url = resource(client, "network", "networks", network["uuid"])
    state = network["driver_state"]
    configured = client.put(
        url, json={"driver_spec": {"driver": "ovs_evpn", "mtu": 1400}}
    ).json()
    assert configured["driver_spec"] == {"driver": "ovs_evpn", "mtu": 1400}
    assert configured["driver_state"]["vni"] == state["vni"]
    preserved = client.put(url, json={"name": "renamed"}).json()
    assert preserved["driver_state"]["mtu"] == 1400
    assert "effective_mtu" not in preserved
    reset = client.put(url, json={"driver_spec": {"driver": "ovs_evpn"}}).json()
    assert reset["driver_spec"] == {"driver": "ovs_evpn"}
    assert reset["driver_state"]["mtu"] != 1400
    assert reset["driver_state"]["vni"] == state["vni"]
    with pytest.raises(exceptions.BadRequestError):
        client.put(url, json={"driver_spec": {}})


def test_address_reuses_without_offline_host_withdrawal_ack(topology):
    from gcl_sdk.agents.universal.dm import models as ua
    from restalchemy.dm import filters

    from exordos_core.user_api.network.dm import models as catalog

    client, network, subnet = topology
    port = create(client, ("network", "ports"), subnet=subnet["uuid"])
    external = create(
        client,
        ("network", "networks"),
        access="public",
        driver_spec={"driver": "ovs_evpn"},
    )
    pool = create(
        client,
        ("network", "subnets"),
        network=external["uuid"],
        cidr="192.168.1.0/24",
        ip_range="192.168.1.50-192.168.1.51",
        placeable=False,
        floating_pool=True,
        dhcp=False,
    )
    fip = create(
        client,
        ("network", "catalog", "addresses"),
        kind="floating",
        subnet=pool["uuid"],
        port=port["uuid"],
    )
    address = catalog.Address.objects.get_one(filters={"uuid": filters.EQ(fip["uuid"])})
    address.claim_publication(uuid.UUID(port["uuid"]))
    guest = create(
        client,
        ("compute", "nodes"),
        name="host",
        cores=1,
        ram=1024,
        disk_spec={"kind": "root_disk", "image": "ubuntu_24.04"},
        default_network={},
    )
    host = uuid.uuid4()
    ua.UniversalAgent(uuid=host, name="host", node=uuid.UUID(guest["uuid"])).insert()
    target = ua.TargetResource(
        uuid=uuid.UUID(port["uuid"]),
        kind="evpn_port",
        agent=host,
        value={
            "fips": [{"public": fip["address"]}],
        },
    )
    target.calculate_hash()
    target.insert()
    actual = ua.Resource(
        uuid=target.uuid,
        kind=target.kind,
        node=uuid.UUID(guest["uuid"]),
        value=target.value,
        hash=target.hash,
        status="ACTIVE",
    )
    actual.insert()
    url = resource(client, "network", "catalog", "addresses", fip["uuid"])
    assert client.get(url).json()["status"] == "ACTIVE"
    client.put(url, json={"port": None})
    assert client.get(url).json()["status"] == "UNBOUND"
    rebound = client.put(url, json={"port": port["uuid"]}).json()
    assert rebound["port"] == port["uuid"]
    client.put(url, json={"port": None})
    client.delete(url)
    reused = create(
        client,
        ("network", "catalog", "addresses"),
        kind="floating",
        subnet=pool["uuid"],
    )
    assert reused["address"] == fip["address"]


def test_parallel_allocations_have_unique_ip_mac_and_index(topology):
    from concurrent.futures import ThreadPoolExecutor

    from exordos_core.compute.dm import models as compute

    client, network, subnet = topology
    node = create(
        client,
        ("compute", "nodes"),
        name="parallel",
        cores=1,
        ram=1024,
        disk_spec={"kind": "root_disk", "image": "ubuntu_24.04"},
        default_network={},
    )

    def allocate(_):
        port = compute.Port(
            project_id=uuid.UUID(int=0),
            subnet=uuid.UUID(subnet["uuid"]),
            node=uuid.UUID(node["uuid"]),
        )
        port.insert()
        return port.index, str(port.ipv4), port.mac

    with ThreadPoolExecutor(max_workers=4) as pool:
        allocated = list(pool.map(allocate, range(8)))
    assert sorted(p[0] for p in allocated) == list(range(8))
    assert len({p[1] for p in allocated}) == len({p[2] for p in allocated}) == 8


def test_private_address_and_mac_reuse_without_offline_host_ack(topology):
    from gcl_sdk.agents.universal.dm import models as ua
    from restalchemy.dm import filters

    from exordos_core.compute.dm import models as compute
    from exordos_core.network.driver import evpn

    client, network, subnet = topology
    port = create(client, ("network", "ports"), subnet=subnet["uuid"])
    host_node = create(
        client,
        ("compute", "nodes"),
        name="host",
        cores=1,
        ram=1024,
        disk_spec={"kind": "root_disk", "image": "ubuntu_24.04"},
        default_network={},
    )
    agent = ua.UniversalAgent(
        uuid=uuid.uuid4(), name="host", node=uuid.UUID(host_node["uuid"])
    )
    agent.insert()
    marker = ua.TargetResource(
        uuid=uuid.UUID(subnet["uuid"]),
        kind="evpn_subnet",
        master=uuid.UUID(network["uuid"]),
        value={},
    )
    marker.calculate_hash()
    marker.insert()
    target = ua.TargetResource(
        uuid=uuid.UUID(port["uuid"]),
        kind="evpn_port",
        master=marker.uuid,
        agent=agent.uuid,
        value={"ipv4": port["ipv4"]},
    )
    target.calculate_hash()
    target.insert()
    url = resource(client, "network", "ports", port["uuid"])
    client.put(url, json={"node": host_node["uuid"]})
    client.put(url, json={"node": None})
    assert (
        client.put(url, json={"node": host_node["uuid"]}).json()["node"]
        == host_node["uuid"]
    )
    client.delete(url)
    reused = create(
        client,
        ("network", "ports"),
        subnet=subnet["uuid"],
        target_ipv4=port["ipv4"],
        target_mac=port["mac"],
    )
    assert reused["ipv4"] == port["ipv4"] and reused["mac"] == port["mac"]
    driver = evpn.OvsEvpnNetworkDriver(
        compute.Network.objects.get_one(filters={"uuid": filters.EQ(network["uuid"])})
    )
    from unittest.mock import patch

    with (
        patch.object(driver, "_collect_host_nfs"),
        patch.object(driver, "_collect_host"),
    ):
        driver.delete_port(compute.Port.restore_from_simple_view(**port))
    assert (
        ua.TargetResource.objects.get_one_or_none(
            filters={"kind": filters.EQ("evpn_port"), "uuid": filters.EQ(target.uuid)}
        )
        is None
    )
    assert (
        client.get(resource(client, "network", "ports", reused["uuid"])).json()["ipv4"]
        == port["ipv4"]
    )


@pytest.mark.parametrize(
    "query",
    [
        {"network": "00000000-0000-0000-0000-000000000000"},
        {"sort_key": "security_groups"},
    ],
)
def test_custom_properties_are_not_sql_filters(topology, query):
    client, _, _ = topology
    with pytest.raises(exceptions.BadRequestError):
        client.get(collection(client, "network", "ports"), params=query)


def test_node_custom_properties_are_not_filter_expressions(topology):
    client, _, _ = topology
    with pytest.raises(exceptions.BadRequestError):
        client.get(
            collection(client, "compute", "nodes"),
            params={"q": 'ports = "00000000-0000-0000-0000-000000000000"'},
        )


def test_duplicate_mac_on_one_node_across_networks_is_rejected(topology):
    client, _, subnet = topology
    node = create(
        client,
        ("compute", "nodes"),
        cores=1,
        ram=1024,
        name="multi",
        disk_spec={"kind": "root_disk", "image": "ubuntu_24.04"},
        default_network={},
    )
    create(
        client,
        ("network", "ports"),
        subnet=subnet["uuid"],
        node=node["uuid"],
        target_mac="52:54:00:01:02:03",
    )
    other = create(client, ("network", "networks"), driver_spec={"driver": "ovs_evpn"})
    second = create(
        client, ("network", "subnets"), network=other["uuid"], cidr="10.102.0.0/24"
    )
    with pytest.raises(exceptions.ConflictError):
        create(
            client,
            ("network", "ports"),
            subnet=second["uuid"],
            node=node["uuid"],
            target_mac="52:54:00:01:02:03",
        )


@pytest.mark.parametrize(
    "value", [None, [{"port": {"network": "00000000-0000-0000-0000-000000000000"}}]]
)
def test_nodeset_rejects_null_or_mixed_legacy_networking(topology, value):
    client, _, _ = topology
    body = dict(
        name="invalid-set",
        cores=1,
        ram=1024,
        disk_spec={"kind": "root_disk", "image": "ubuntu_24.04"},
        networking=value,
    )
    with pytest.raises(exceptions.BadRequestError):
        create(client, ("compute", "sets"), **body)


def test_declarative_reapply_preserves_generated_and_independent_fields(
    topology, tmp_path
):
    from gcl_sdk.agents.universal.clients.backend.db import DatabaseBackendClient
    from gcl_sdk.agents.universal.clients.backend.db import ModelSpec
    from gcl_sdk.agents.universal.dm import models as ua
    from gcl_sdk.agents.universal.storage.fs import TargetFieldsFileStorage
    from restalchemy.dm import filters

    from exordos_core.compute.dm import models as compute
    from exordos_core.user_api.network.dm import models as catalog

    client, network, subnet = topology
    storage = TargetFieldsFileStorage(str(tmp_path / "targets.json"))
    backend = DatabaseBackendClient(
        [
            ModelSpec(
                compute.Node,
                "node",
                filters={},
            ),
            ModelSpec(
                compute.Port,
                "port",
                filters={},
            ),
            ModelSpec(compute.Network, "network", filters={}),
            ModelSpec(catalog.NetworkFunction, "nf", filters={}),
        ],
        storage,
    )

    def declaration(kind, value):
        value = {"project_id": str(uuid.UUID(int=0)), **value}
        return ua.Resource.from_value(value, kind, frozenset(value))

    node_id = str(uuid.uuid4())
    node_value = {
        "uuid": node_id,
        "project_id": str(uuid.UUID(int=0)),
        "name": "declarative",
        "cores": 1,
        "ram": 1024,
        "disk_spec": {"kind": "root_disk", "image": "ubuntu_24.04"},
        "default_network": {"network": network["uuid"]},
    }
    node = backend.create(declaration("node", node_value))
    original = node.default_network
    port_id = original["port"]
    group = create(
        client, ("network", "catalog", "security_groups"), name="independent"
    )
    create(
        client,
        ("network", "port_security_groups"),
        port=port_id,
        security_group=group["uuid"],
    )
    backend.update(declaration("node", {**node_value, "default_network": {}}))
    assert backend.get(declaration("node", node_value)).default_network == original
    updated = backend.update(declaration("port", {"uuid": port_id, "name": "changed"}))
    assert updated.index == 0 and updated.security_groups == [uuid.UUID(group["uuid"])]
    stored = compute.Network.objects.get_one(
        filters={"uuid": filters.EQ(network["uuid"])}
    )
    backend.update(
        declaration(
            "network",
            {
                "uuid": network["uuid"],
                "driver_spec": {"driver": "ovs_evpn", "mtu": 1400},
            },
        )
    )
    backend.update(declaration("network", {"uuid": network["uuid"], "name": "renamed"}))
    assert backend.get(
        declaration("network", {"uuid": network["uuid"]})
    ).driver_spec == {"driver": "ovs_evpn", "mtu": 1400}
    backend.update(
        declaration(
            "network", {"uuid": network["uuid"], "driver_spec": {"driver": "ovs_evpn"}}
        )
    )
    current = backend.get(declaration("network", {"uuid": network["uuid"]}))
    assert current.driver_spec == {"driver": "ovs_evpn"}
    assert current.driver_state["vni"] == stored.driver_state["vni"]

    nf = catalog.NetworkFunction(
        project_id=stored.project_id,
        kind="dns",
        config={"zone_suffix": "old", "forwarders": ["8.8.8.8"]},
    )
    nf.insert()
    updated = backend.update(
        declaration(
            "nf",
            {"uuid": str(nf.uuid), "kind": nf.kind, "config": {"zone_suffix": "new"}},
        )
    )
    assert updated.config == {"zone_suffix": "new"}
    reset = backend.update(
        declaration("nf", {"uuid": str(nf.uuid), "kind": nf.kind, "config": {}})
    )
    assert reset.config == {}


def test_nodeset_template_changes_only_new_nodes(topology):
    from restalchemy.dm import filters

    from exordos_core.compute.dm import models as compute
    from exordos_core.compute.node_set.dm import models as sets

    client, network, _ = topology
    body = create(
        client,
        ("compute", "sets"),
        name="set",
        cores=1,
        ram=1024,
        replicas=1,
        disk_spec={"kind": "root_disk", "image": "ubuntu_24.04"},
        default_network={"network": network["uuid"]},
    )
    node_set = sets.NodeSet.objects.get_one(filters={"uuid": filters.EQ(body["uuid"])})
    first = node_set.gen_nodes(project_id=node_set.project_id)[0]
    first.insert()
    original = list(node_ports(client, first))
    client.put(
        resource(client, "compute", "sets", body["uuid"]), json={"default_network": {}}
    )
    node_set = sets.NodeSet.objects.get_one(filters={"uuid": filters.EQ(body["uuid"])})
    second = node_set.gen_nodes(project_id=node_set.project_id)[0]
    second.insert()
    assert node_ports(client, second) == []
    assert (
        node_ports(
            client,
            compute.Node.objects.get_one(filters={"uuid": filters.EQ(first.uuid)}),
        )
        == original
    )


def test_networking_failure_rolls_back_node_and_ports(topology, monkeypatch):
    from restalchemy.dm import filters

    from exordos_core.compute.dm import models as compute

    client, network, _ = topology
    node_id = str(uuid.uuid4())
    body = dict(
        uuid=node_id,
        project_id=str(uuid.UUID(int=0)),
        name="networking-rollback",
        cores=1,
        ram=1024,
        disk_spec={"kind": "root_disk", "image": "ubuntu_24.04"},
        default_network={"network": network["uuid"]},
    )

    insert_port = compute.Port.insert

    def failure(port, *args, **kwargs):
        insert_port(port, *args, **kwargs)
        raise RuntimeError("injected networking failure")

    with monkeypatch.context() as patch:
        patch.setattr(compute.Port, "insert", failure)
        with pytest.raises(exceptions.BaseHTTPException):
            client.post(
                collection(client, "compute", "nodes"),
                json=body,
            )
    assert (
        compute.Node.objects.get_one_or_none(filters={"uuid": filters.EQ(node_id)})
        is None
    )
    assert compute.Port.objects.get_all(filters={"node": filters.EQ(node_id)}) == []
    response = client.post(
        collection(client, "compute", "nodes"),
        json=body,
    )
    assert response.status_code == 201
    assert len(node_ports(client, response.json())) == 1


def test_parallel_overlapping_subnets_do_not_both_succeed(topology):
    from concurrent.futures import ThreadPoolExecutor

    import netaddr

    from exordos_core.common import exceptions as core_exc
    from exordos_core.compute.dm import models as compute

    client, _, _ = topology
    network = create(
        client, ("network", "networks"), driver_spec={"driver": "ovs_evpn"}
    )

    def insert(_):
        subnet = compute.Subnet(
            network=uuid.UUID(network["uuid"]),
            project_id=uuid.UUID(int=0),
            cidr=netaddr.IPNetwork("10.201.0.0/24"),
        )
        try:
            subnet.insert()
            return True
        except core_exc.ValidateException:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(insert, range(2)))
    assert sorted(result) == [False, True]


@pytest.mark.parametrize("floating", [False, True])
def test_explicit_foreign_address_permission_covers_port_and_fip(
    topology, user_api_client, auth_test1_user, floating
):
    import netaddr

    from exordos_core.compute.dm import models as compute

    admin, network, _ = topology
    admin.put(
        resource(admin, "network", "networks", network["uuid"]),
        json={"access": "public"},
    )
    external = compute.Network(
        project_id=uuid.uuid4(), access="public", driver_spec={"driver": "ovs_evpn"}
    )
    external.insert()
    pool = compute.Subnet(
        project_id=external.project_id,
        network=external.uuid,
        cidr=netaddr.IPNetwork("192.168.90.0/24"),
        floating_pool=True,
        placeable=not floating,
        dhcp=False,
    )
    pool.insert()
    permissions = [
        "compute.node.create",
        "network.port.create",
        "network.address.create",
    ]
    creator = user_api_client(auth_test1_user, permissions=permissions)
    if floating:
        path = ("network", "catalog", "addresses")
        body = {
            "project_id": str(uuid.UUID(int=0)),
            "kind": "floating",
            "network": str(external.uuid),
            "address": "192.168.90.50",
        }
    else:
        path = ("network", "ports")
        body = {
            "project_id": str(uuid.UUID(int=0)),
            "network": str(external.uuid),
            "target_ipv4": "192.168.90.50",
        }
    with pytest.raises(exceptions.ForbiddenError):
        creator.post(collection(creator, *path), json=body)
    pinner = user_api_client(
        auth_test1_user, permissions=permissions + ["network.address.explicit_address"]
    )
    assert pinner.post(collection(pinner, *path), json=body).status_code == 201


def test_default_port_requires_only_node_create_permission(
    topology, user_api_client, auth_test1_user
):
    _, network, _ = topology
    caller = user_api_client(auth_test1_user, permissions=["compute.node.create"])
    body = {
        "project_id": str(uuid.UUID(int=0)),
        "cores": 1,
        "ram": 1024,
        "name": "no-port-permission",
        "disk_spec": {"kind": "root_disk", "image": "ubuntu_24.04"},
        "default_network": {"network": network["uuid"]},
    }
    assert (
        caller.post(collection(caller, "compute", "nodes"), json=body).status_code
        == 201
    )


def test_owner_role_contains_new_resource_permissions(user_api):
    from restalchemy.dm import filters

    from exordos_core.common import constants
    from exordos_core.user_api.iam.dm import models

    owner = models.Role.objects.get_one(
        filters={"uuid": filters.EQ(uuid.UUID(constants.OWNER_ROLE_UUID))}
    )
    names = {permission.name for permission in owner.get_permissions()._permissions}
    for resource in (
        "security_group_rule",
        "port_security_group",
    ):
        assert {
            f"network.{resource}.{action}"
            for action in ("read", "create", "update", "delete")
        } <= names
    assert "network.address.explicit_address" not in names
    assert "network.subnet.floating_pool" not in names


@pytest.mark.parametrize(
    "kind,parent",
    [
        ("security_group_rule", "security_group"),
        ("port_security_group", "port"),
    ],
)
def test_standalone_bindings_and_rules_require_dedicated_permissions(
    topology, user_api_client, auth_test1_user, kind, parent
):

    caller = user_api_client(auth_test1_user, permissions=[f"network.{parent}.create"])
    admin, network, _ = topology
    port = create(admin, ("network", "ports"), network=network["uuid"])
    sg = create(admin, ("network", "catalog", "security_groups"), name="permission-sg")
    if kind == "security_group_rule":
        path = ("network", "catalog", "security_group_rules")
        body = {
            "security_group": sg["uuid"],
            "direction": "ingress",
            "protocol": "tcp",
            "port": 22,
        }
    else:
        path = ("network", kind + "s")
        body = {
            "port": port["uuid"],
            "security_group": sg["uuid"],
        }
    body["project_id"] = str(uuid.UUID(int=0))
    with pytest.raises(exceptions.ForbiddenError):
        caller.post(collection(caller, *path), json=body)


def test_floating_pool_is_operator_approved_independently_of_access(
    topology, user_api_client, auth_test1_user, auth_test2_user
):
    _, network, _ = topology
    creator = user_api_client(auth_test1_user, permissions=["network.subnet.create"])
    body = {
        "project_id": str(uuid.UUID(int=0)),
        "network": network["uuid"],
        "cidr": "192.168.91.0/24",
        "floating_pool": True,
        "placeable": False,
        "dhcp": False,
    }
    with pytest.raises(exceptions.ForbiddenError):
        creator.post(collection(creator, "network", "subnets"), json=body)
    operator = user_api_client(
        auth_test1_user, permissions=["network.subnet.floating_pool"]
    )
    pool = operator.post(collection(operator, "network", "subnets"), json=body).json()
    allocator = user_api_client(auth_test1_user, permissions=["network.address.create"])
    address = create(
        allocator,
        ("network", "catalog", "addresses"),
        kind="floating",
        subnet=pool["uuid"],
    )
    assert address["subnet"] == pool["uuid"]
    updater = user_api_client(auth_test2_user, permissions=["network.subnet.update"])
    with pytest.raises(exceptions.ForbiddenError):
        updater.put(
            resource(updater, "network", "subnets", pool["uuid"]),
            json={"floating_pool": False},
        )


def test_floating_address_is_unique_across_external_networks(topology):
    client, _, _ = topology
    pools = []
    for number in range(2):
        network = create(
            client,
            ("network", "networks"),
            name=f"external-{number}",
            access="public",
            driver_spec={"driver": "ovs_evpn"},
        )
        pools.append(
            create(
                client,
                ("network", "subnets"),
                network=network["uuid"],
                cidr="192.0.2.0/24",
                ip_range="192.0.2.10-192.0.2.20",
                floating_pool=True,
                placeable=False,
                dhcp=False,
            )
        )
    first = create(
        client,
        ("network", "catalog", "addresses"),
        kind="floating",
        subnet=pools[0]["uuid"],
        address="192.0.2.10",
    )
    with pytest.raises(exceptions.ConflictError):
        create(
            client,
            ("network", "catalog", "addresses"),
            kind="floating",
            subnet=pools[1]["uuid"],
            address=first["address"],
        )
    second = create(
        client,
        ("network", "catalog", "addresses"),
        kind="floating",
        subnet=pools[1]["uuid"],
    )
    assert second["address"] == "192.0.2.11"
    client.delete(resource(client, "network", "catalog", "addresses", first["uuid"]))
    reused = create(
        client,
        ("network", "catalog", "addresses"),
        kind="floating",
        subnet=pools[1]["uuid"],
    )
    assert reused["address"] == "192.0.2.10"


def test_database_rejects_floating_collision_between_subnets(topology):
    import psycopg
    from restalchemy.common import contexts

    client, _, _ = topology
    pools = []
    for number in range(2):
        network = create(
            client,
            ("network", "networks"),
            name=f"external-{number}",
            access="public",
            driver_spec={"driver": "ovs_evpn"},
        )
        pools.append(
            create(
                client,
                ("network", "subnets"),
                network=network["uuid"],
                cidr="192.0.2.0/24",
                floating_pool=True,
                placeable=False,
                dhcp=False,
            )
        )
    address = create(
        client,
        ("network", "catalog", "addresses"),
        kind="floating",
        subnet=pools[0]["uuid"],
        address="192.0.2.10",
    )
    # Bypass model validation to reproduce concurrent allocations that both
    # observed a free address. The database must reject the second claimant.
    with pytest.raises(psycopg.errors.UniqueViolation):
        with contexts.Context().session_manager() as session:
            session.execute(
                "INSERT INTO net_addresses "
                "(uuid, project_id, created_at, updated_at, subnet, address, kind) "
                "SELECT %s, project_id, created_at, updated_at, %s, address, kind "
                "FROM net_addresses WHERE uuid=%s",
                (str(uuid.uuid4()), pools[1]["uuid"], address["uuid"]),
            )


def test_two_nic_targets_keep_secondary_fip_and_advertise_one_default(topology):
    import socket

    from gcl_sdk.agents.universal.drivers import evpn_nf
    from gcl_sdk.agents.universal.drivers import evpn as dp_models
    from restalchemy.dm import filters

    from exordos_core.compute.dm import models as compute
    from exordos_core.tests.functional.restapi.network.test_default_network import pool

    client, network, subnet = topology
    node = create(
        client,
        ("compute", "nodes"),
        name="two-nic-dhcp",
        cores=1,
        ram=1024,
        disk_spec={"kind": "root_disk", "image": "ubuntu_24.04"},
        default_network={},
    )
    ports = [
        create(
            client,
            ("network", "ports"),
            subnet=subnet["uuid"],
            node=node["uuid"],
        )
        for _ in range(2)
    ]
    public = pool(client)
    address = create(
        client,
        ("network", "catalog", "addresses"),
        kind="floating",
        subnet=public["uuid"],
        port=ports[1]["uuid"],
    )
    stored_subnet = compute.Subnet.objects.get_one(
        filters={"uuid": filters.EQ(subnet["uuid"])}
    )
    driver = compute.Network.objects.get_one(
        filters={"uuid": filters.EQ(network["uuid"])}
    ).load_driver()
    for index, view in enumerate(ports):
        port = compute.Port.objects.get_one(filters={"uuid": filters.EQ(view["uuid"])})
        target = driver._build_evpn_port(
            port, stored_subnet, publish=False
        ).to_ua_resource(master=stored_subnet.uuid)
        compiled = dp_models.EvpnPort.from_ua_resource(target)
        assert compiled.dhcp["default_route"] is (index == 0)
        assert compiled.dhcp["routers"] == [{"to": "0.0.0.0/0", "via": "10.101.0.1"}]
        assert compiled.fips == ([] if index == 0 else [{"public": address["address"]}])
        record = dict(compiled.dhcp, ipv4=compiled.ipv4)
        reply = evpn_nf.build_reply(
            b"\x00" * 4,
            0x8000,
            compiled.mac,
            evpn_nf.ACK,
            record,
            record["cidr"],
            "10.101.0.1",
        )
        options = {}
        position = 240
        while reply[position] != 255:
            code, length = reply[position : position + 2]
            options[code] = (
                options.get(code, b"") + reply[position + 2 : position + 2 + length]
            )
            position += length + 2
        assert (3 in options) is (index == 0)
        assert (121 in options) is (index == 0)
        if index == 0:
            assert options[121] == b"\x00" + socket.inet_aton("10.101.0.1")
