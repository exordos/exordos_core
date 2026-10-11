#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

import uuid

from bazooka import exceptions
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


def test_node_and_independent_port_bindings(topology):
    client, network, subnet = topology
    node = create(
        client,
        ("compute", "nodes"),
        name="guest",
        cores=1,
        ram=1024,
        disk_spec={"kind": "root_disk", "image": "ubuntu_24.04"},
        networking=[],
    )
    assert node["ports"] == []
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
    assert client.get(resource(client, "compute", "nodes", node["uuid"])).json()[
        "ports"
    ] == [port["uuid"]]
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


def test_inline_create_is_atomic_and_reserves_explicit_indexes_first(topology):
    client, network, subnet = topology
    node = create(
        client,
        ("compute", "nodes"),
        name="two-nics",
        cores=1,
        ram=1024,
        disk_spec={"kind": "root_disk", "image": "ubuntu_24.04"},
        networking=[
            {"port": {"network": network["uuid"]}},
            {"port": {"subnet": subnet["uuid"]}, "index": 0},
        ],
    )
    ports = [
        client.get(resource(client, "network", "ports", p)).json()
        for p in node["ports"]
    ]
    assert sorted(p["index"] for p in ports) == [0, 1]
    assert len(node["ports"]) == 2 and "created_resources" not in node
    bad_uuid = str(uuid.uuid4())
    with pytest.raises(exceptions.BadRequestError):
        create(
            client,
            ("compute", "nodes"),
            uuid=bad_uuid,
            name="bad",
            cores=1,
            ram=1024,
            disk_spec={"kind": "root_disk", "image": "ubuntu_24.04"},
            networking=[
                {"port": {"network": network["uuid"]}},
                {
                    "port": {"network": network["uuid"]},
                    "security_groups": [str(uuid.uuid4())],
                },
            ],
        )
    with pytest.raises(exceptions.NotFoundError):
        client.get(resource(client, "compute", "nodes", bad_uuid))
    assert all(
        p.get("node") != bad_uuid
        for p in client.get(collection(client, "network", "ports")).json()
    )


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
        networking=[],
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
            networking=[{"port": port}],
        )


def test_subnet_defaults_preserve_omitted_and_distinguish_empty(topology):
    client, network, subnet = topology
    url = resource(client, "network", "subnets", subnet["uuid"])
    assert (
        subnet.get("routers") is None
        and subnet["effective_routers"][0]["via"] == "10.101.0.1"
    )
    assert (
        client.put(url, json={"name": "renamed"}).json()["effective_routers"]
        == subnet["effective_routers"]
    )
    assert (
        client.put(url, json={"routers": [], "dns_servers": []}).json()[
            "effective_routers"
        ]
        == []
    )
    reset = client.put(url, json={"routers": None, "dns_servers": None}).json()
    assert reset["effective_routers"] == subnet["effective_routers"]
    assert reset["effective_dns_servers"] == ["10.101.0.1"]
    assert client.put(url, json={"network": network["uuid"]}).status_code == 200


def test_network_partial_settings_do_not_overwrite_allocated_state(topology):
    client, network, subnet = topology
    url = resource(client, "network", "networks", network["uuid"])
    state = network["driver_state"]
    configured = client.put(url, json={"driver_spec": {"mtu": 1400}}).json()
    assert configured["driver_spec"] == {"driver": "ovs_evpn", "mtu": 1400}
    assert configured["driver_state"]["vni"] == state["vni"]
    preserved = client.put(url, json={"driver_spec": {}}).json()
    assert preserved["driver_state"]["mtu"] == 1400
    assert "effective_mtu" not in preserved
    reset = client.put(url, json={"driver_spec": {"mtu": None}}).json()
    assert reset["driver_state"]["mtu"] != 1400
    assert reset["driver_state"]["vni"] == state["vni"]


def test_automatic_identity_covers_network_and_is_protected(topology):
    client, network, subnet = topology
    create(
        client, ("network", "subnets"), network=network["uuid"], cidr="10.102.0.0/24"
    )
    identity = client.get(
        resource(
            client,
            "network",
            "catalog",
            "identity_groups",
            network["default_identity_group"],
        )
    ).json()
    assert identity["automatic"] and identity["network"] == network["uuid"]
    with pytest.raises(exceptions.ConflictError):
        client.delete(
            resource(client, "network", "catalog", "identity_groups", identity["uuid"])
        )


def test_address_stays_reserved_until_current_host_withdrawal_ack(topology):
    from gcl_sdk.agents.universal.dm import models as ua
    from restalchemy.dm import filters

    from exordos_core.compute.dm import models as compute
    from exordos_core.network.driver import evpn
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
        networking=[],
    )
    host = uuid.uuid4()
    ua.UniversalAgent(uuid=host, name="host", node=uuid.UUID(guest["uuid"])).insert()
    target = ua.TargetResource(
        uuid=uuid.UUID(port["uuid"]),
        kind="evpn_port",
        agent=host,
        value={
            "enabled": True,
            "fips": [{"public": fip["address"]}],
            "retired_fips": [],
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
    target.value = {"enabled": True, "fips": [], "retired_fips": [fip["address"]]}
    target.calculate_hash()
    import datetime

    target.tracked_at = datetime.datetime.now(datetime.timezone.utc)
    target.update()
    driver = evpn.OvsEvpnNetworkDriver(
        compute.Network.objects.get_one(filters={"uuid": filters.EQ(network["uuid"])})
    )
    driver._settle_withdrawals()
    assert client.get(url).json()["status"] == "IN_PROGRESS"
    # An offline or stale host cannot make its address reusable.
    second = create(
        client,
        ("network", "catalog", "addresses"),
        kind="floating",
        subnet=pool["uuid"],
    )
    assert second["address"] == "192.168.1.51"
    actual.hash = target.hash
    actual.node = uuid.uuid4()
    actual.update()
    driver._settle_withdrawals()
    assert client.get(url).json()["status"] == "IN_PROGRESS"
    actual.node = uuid.UUID(guest["uuid"])
    actual.update()
    from exordos_core.network.contracts import applied_by_owner

    observed = ua.Resource.objects.get_one(
        filters={"res_uuid": filters.EQ(actual.res_uuid)}
    )
    assert applied_by_owner(target, observed), (
        target.tracked_at,
        observed.updated_at,
        target.hash,
        observed.hash,
        observed.node,
    )
    stored_target = ua.TargetResource.objects.get_one(
        filters={"res_uuid": filters.EQ(target.res_uuid)}
    )
    assert driver._withdrawal_acknowledged(stored_target), (
        stored_target.dump_to_simple_view(),
        observed.dump_to_simple_view(),
    )
    driver._settle_withdrawals()
    assert client.get(url).json()["status"] == "UNBOUND"
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
        networking=[],
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


def test_private_address_is_held_until_interface_withdrawal_ack(topology):
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
        networking=[],
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
        value={"enabled": False, "ipv4": port["ipv4"]},
    )
    target.calculate_hash()
    target.insert()
    client.delete(resource(client, "network", "ports", port["uuid"]))
    next_port = create(client, ("network", "ports"), subnet=subnet["uuid"])
    assert next_port["ipv4"] != port["ipv4"]
    with pytest.raises(exceptions.ConflictError):
        create(
            client,
            ("network", "ports"),
            subnet=subnet["uuid"],
            target_ipv4=port["ipv4"],
        )
    actual = ua.Resource(
        uuid=target.uuid,
        kind=target.kind,
        node=agent.node,
        value=target.value,
        hash=target.hash,
        status="ACTIVE",
    )
    actual.insert()
    driver = evpn.OvsEvpnNetworkDriver(
        compute.Network.objects.get_one(filters={"uuid": filters.EQ(network["uuid"])})
    )
    from unittest.mock import patch

    with (
        patch.object(driver, "_collect_host_nfs"),
        patch.object(driver, "_collect_host"),
    ):
        driver._settle_withdrawals()
    reused = create(client, ("network", "ports"), subnet=subnet["uuid"])
    assert reused["ipv4"] == port["ipv4"]


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


def test_nodeset_barrier_survives_reload_before_replica_creation(topology, tmp_path):
    from gcl_sdk.agents.universal.clients.backend.db import DatabaseBackendClient
    from gcl_sdk.agents.universal.clients.backend.db import ModelSpec
    from gcl_sdk.agents.universal.drivers.direct import DirectAgentDriver
    from gcl_sdk.agents.universal.storage.fs import TargetFieldsFileStorage
    from restalchemy.dm import filters

    from exordos_core.compute.dm import models as compute
    from exordos_core.compute.node_set.dm import models as node_set_models
    from exordos_core.elements.dm import models as em
    from exordos_core.network.manifest import barrier_ready
    from exordos_core.user_api.network.dm import models as catalog

    client, _, subnet = topology
    group = catalog.SecurityGroup(project_id=uuid.UUID(int=0))
    group.insert()
    core = em.Manifest(name="core", version="1.0.0", resources={})
    core.save()
    core.install()
    manifest = em.Manifest(
        name="guarded_replicas",
        version="1.0.0",
        requirements={"core": {"from_version": "0.0.0"}},
        resources={
            "$core.compute.sets": {
                "guests": {
                    "project_id": str(group.project_id),
                    "cores": 1,
                    "ram": 1024,
                    "replicas": 1,
                    "disk_spec": {"kind": "root_disk", "image": "ubuntu_24.04"},
                    "networking": [
                        {
                            "port": {"subnet": subnet["uuid"]},
                            "security_groups": [str(group.uuid)],
                        }
                    ],
                }
            },
            "$core.network.catalog.security_group_rules": {
                "ingress": {
                    "project_id": str(group.project_id),
                    "security_group": str(group.uuid),
                    "direction": "ingress",
                }
            },
        },
    )
    manifest.save()
    manifest.validate()
    manifest.install()
    declaration = next(
        item
        for item in em.element_engine.get_resources()
        if isinstance(item, em.Resource) and item.kind == "em_core_compute_sets"
    )
    declaration.actualize()
    request = declaration.target_resource
    rule_id = uuid.UUID(request.value["network_activation_barrier"]["rules"][0])
    storage = TargetFieldsFileStorage(str(tmp_path / "target-fields.json"))
    backend = DatabaseBackendClient(
        [ModelSpec(node_set_models.NodeSet, request.kind, filters={})], storage
    )

    class Driver(DirectAgentDriver):
        def get_capabilities(self):
            return [request.kind]

    driver = Driver(backend, storage)
    response = driver.create(request)
    assert response.hash == request.hash
    assert driver.get(request).hash == request.hash
    assert driver.update(request).hash == request.hash
    restored = node_set_models.NodeSet.objects.get_one(
        filters={"uuid": filters.EQ(request.uuid)}
    )
    node = restored.gen_nodes(group.project_id)[0]
    node.insert()
    port = compute.Port.objects.get_one(filters={"node": filters.EQ(node.uuid)})
    assert port.activation_barrier == {"rules": [str(rule_id)]}
    assert not port.ready_for_activation()
    catalog.SecurityGroupRule(
        uuid=rule_id,
        project_id=group.project_id,
        security_group=group.uuid,
        direction="ingress",
    ).insert()
    assert barrier_ready(port.activation_barrier)
    assert port.ready_for_activation() and port.activation_barrier == {}
    response = client.get(resource(client, "compute", "sets", str(request.uuid))).json()
    assert "network_activation_barrier" not in response


def test_duplicate_mac_on_one_node_across_networks_is_rejected(topology):
    client, _, subnet = topology
    node = create(
        client,
        ("compute", "nodes"),
        cores=1,
        ram=1024,
        name="multi",
        disk_spec={"kind": "root_disk", "image": "ubuntu_24.04"},
        networking=[],
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
    if value is not None:
        body["interfaces"] = []
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
            ModelSpec(compute.Node, "node", filters={}),
            ModelSpec(compute.Port, "port", filters={}),
            ModelSpec(compute.Network, "network", filters={}),
            ModelSpec(catalog.NetworkFunction, "nf", filters={}),
        ],
        storage,
    )

    def declaration(kind, value):
        return ua.Resource.from_value(value, kind, frozenset(value))

    node_id = str(uuid.uuid4())
    node_value = {
        "uuid": node_id,
        "project_id": str(uuid.UUID(int=0)),
        "name": "declarative",
        "cores": 1,
        "ram": 1024,
        "disk_spec": {"kind": "root_disk", "image": "ubuntu_24.04"},
        "networking": [{"port": {"network": network["uuid"]}}],
    }
    node = backend.create(declaration("node", node_value))
    original = list(node.ports)
    port_id = str(original[0])
    group = create(
        client, ("network", "catalog", "security_groups"), name="independent"
    )
    create(
        client,
        ("network", "port_security_groups"),
        port=port_id,
        security_group=group["uuid"],
    )
    backend.update(declaration("node", {**node_value, "networking": []}))
    assert list(backend.get(declaration("node", node_value)).ports) == original
    updated = backend.update(declaration("port", {"uuid": port_id, "name": "changed"}))
    assert updated.index == 0 and updated.security_groups == [uuid.UUID(group["uuid"])]
    # Dictionary patches keep user intent, not resolved defaults.
    stored = compute.Network.objects.get_one(
        filters={"uuid": filters.EQ(network["uuid"])}
    )
    backend.update(
        declaration("network", {"uuid": network["uuid"], "driver_spec": {"mtu": 1400}})
    )
    backend.update(declaration("network", {"uuid": network["uuid"], "driver_spec": {}}))
    assert backend.get(
        declaration("network", {"uuid": network["uuid"]})
    ).driver_spec == {"driver": "ovs_evpn", "mtu": 1400}
    backend.update(
        declaration("network", {"uuid": network["uuid"], "driver_spec": {"mtu": None}})
    )
    current = backend.get(declaration("network", {"uuid": network["uuid"]}))
    assert current.driver_spec["mtu"] is None
    assert current.driver_state["vni"] == stored.driver_state["vni"]


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
        networking=[{"port": {"network": network["uuid"]}}],
    )
    node_set = sets.NodeSet.objects.get_one(filters={"uuid": filters.EQ(body["uuid"])})
    first = node_set.gen_nodes(project_id=node_set.project_id)[0]
    first.insert()
    original = list(first.ports)
    client.put(
        resource(client, "compute", "sets", body["uuid"]), json={"networking": []}
    )
    node_set = sets.NodeSet.objects.get_one(filters={"uuid": filters.EQ(body["uuid"])})
    second = node_set.gen_nodes(project_id=node_set.project_id)[0]
    second.insert()
    assert second.ports == []
    assert (
        compute.Node.objects.get_one(filters={"uuid": filters.EQ(first.uuid)}).ports
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
        networking=[{"port": {"network": network["uuid"]}}],
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
    assert len(response.json()["ports"]) == 1


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


def test_contract_conversion_preserves_flat_nodes(topology):
    import importlib.util
    from pathlib import Path

    import netaddr
    from restalchemy.common import contexts
    from restalchemy.dm import filters

    from exordos_core.compute.dm import models as compute
    from exordos_core.user_api.network.dm import models as catalog

    client, network, _ = topology
    node = create(
        client,
        ("compute", "nodes"),
        name="legacy",
        cores=1,
        ram=1024,
        disk_spec={"kind": "root_disk", "image": "ubuntu_24.04"},
    )
    flat = compute.Network(
        name="flat",
        project_id=uuid.UUID(int=0),
        driver_spec={"driver": "flat_bridge", "path": "/tmp/flat.test"},
    )
    flat.insert()
    subnet = compute.Subnet(
        network=flat.uuid,
        project_id=flat.project_id,
        cidr=netaddr.IPNetwork("10.210.0.0/24"),
    )
    subnet.insert()
    port = compute.Port(
        subnet=subnet.uuid,
        node=uuid.UUID(node["uuid"]),
        project_id=flat.project_id,
        provenance="generated",
    )
    port.insert()
    group = create(client, ("network", "catalog", "security_groups"), name="migration")
    create(
        client,
        ("network", "catalog", "security_group_rules"),
        security_group=group["uuid"],
        direction="ingress",
        protocol="tcp",
        port=22,
    )
    create(
        client,
        ("network", "port_security_groups"),
        port=str(port.uuid),
        security_group=group["uuid"],
    )
    private_pool = compute.Subnet(
        network=uuid.UUID(network["uuid"]),
        project_id=flat.project_id,
        cidr=netaddr.IPNetwork("192.168.92.0/24"),
        placeable=False,
        shared_pool=True,
    )
    private_pool.insert()
    approved_old_pool = compute.Subnet(
        network=flat.uuid,
        project_id=flat.project_id,
        cidr=netaddr.IPNetwork("192.168.93.0/24"),
        placeable=False,
    )
    approved_old_pool.insert()
    path = Path(__file__).parents[5] / "migrations" / "0006-sdn-api-contract-15df7a.py"
    spec = importlib.util.spec_from_file_location("contract_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with contexts.Context().session_manager() as session:
        session.execute("DROP INDEX net_addresses_floating_reserved_idx", None)
        module.migration_step._downgrade_contract(session)
        row = session.execute(
            "SELECT driver_spec FROM compute_networks WHERE uuid=%s", (str(flat.uuid),)
        ).fetchone()
        assert row["driver_spec"] == flat.driver_spec
        legacy = session.execute(
            "SELECT config FROM compute_ports WHERE uuid=%s", (str(port.uuid),)
        ).fetchone()
        assert legacy["config"]["security_groups"] == [group["uuid"]]
        module.migration_step._upgrade_contract(session)
        session.execute(
            "CREATE UNIQUE INDEX net_addresses_floating_reserved_idx "
            "ON net_addresses(address) WHERE kind = 'floating' "
            "AND allocation = 'reserved'",
            None,
        )
    assert not compute.Subnet.objects.get_one(
        filters={"uuid": filters.EQ(private_pool.uuid)}
    ).floating_pool
    assert compute.Subnet.objects.get_one(
        filters={"uuid": filters.EQ(approved_old_pool.uuid)}
    ).floating_pool
    restored = compute.Port.objects.get_one(filters={"uuid": filters.EQ(port.uuid)})
    assert (
        restored.node == port.node
        and restored.index == 0
        and restored.ipv4 == port.ipv4
    )
    assert restored.security_groups == [uuid.UUID(group["uuid"])]
    rules = catalog.SecurityGroup.objects.get_one(
        filters={"uuid": filters.EQ(group["uuid"])}
    ).rules
    assert rules == [{"direction": "ingress", "protocol": "tcp", "port": 22}]
    automatic = catalog.IdentityGroup.objects.get_one(
        filters={"uuid": filters.EQ(network["default_identity_group"])}
    )
    assert automatic.automatic


@pytest.mark.parametrize("via_wrapper", [False, True])
def test_explicit_foreign_address_permission_covers_port_and_wrapper(
    topology, user_api_client, auth_test1_user, via_wrapper
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
        placeable=not via_wrapper,
        dhcp=False,
    )
    pool.insert()
    permissions = [
        "compute.node.create",
        "network.port.create",
        "network.address.create",
    ]
    creator = user_api_client(auth_test1_user, permissions=permissions)
    if via_wrapper:
        path = ("compute", "nodes")
        body = {
            "project_id": str(uuid.UUID(int=0)),
            "cores": 1,
            "ram": 1024,
            "name": "pin-wrapper",
            "disk_spec": {"kind": "root_disk", "image": "ubuntu_24.04"},
            "networking": [
                {
                    "port": {"network": network["uuid"]},
                    "floating_ip": {
                        "network": str(external.uuid),
                        "address": "192.168.90.50",
                    },
                }
            ],
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


def test_creation_wrapper_requires_canonical_resource_permissions(
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
        "networking": [{"port": {"network": network["uuid"]}}],
    }
    with pytest.raises(exceptions.ForbiddenError):
        caller.post(collection(caller, "compute", "nodes"), json=body)


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
        "port_identity_group",
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
        ("port_identity_group", "port"),
    ],
)
def test_standalone_bindings_and_rules_require_dedicated_permissions(
    topology, user_api_client, auth_test1_user, kind, parent
):

    caller = user_api_client(auth_test1_user, permissions=[f"network.{parent}.create"])
    admin, network, _ = topology
    port = create(admin, ("network", "ports"), network=network["uuid"])
    sg = create(admin, ("network", "catalog", "security_groups"), name="permission-sg")
    ig = network["default_identity_group"]
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
            "security_group" if kind == "port_security_group" else "identity_group": sg[
                "uuid"
            ]
            if kind == "port_security_group"
            else ig,
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
                "(uuid, project_id, created_at, updated_at, subnet, address, allocation, origin, kind) "
                "SELECT %s, project_id, created_at, updated_at, %s, address, allocation, origin, kind "
                "FROM net_addresses WHERE uuid=%s",
                (str(uuid.uuid4()), pools[1]["uuid"], address["uuid"]),
            )
