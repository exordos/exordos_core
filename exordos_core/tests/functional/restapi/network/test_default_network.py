# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0

from concurrent.futures import ThreadPoolExecutor
import json
import uuid

from bazooka import exceptions
import pytest
from restalchemy.dm import filters
from restalchemy.storage.sql import orm

from exordos_core.common import config
from exordos_core.compute import constants as nc
from exordos_core.compute.dm import models as compute
from exordos_core.compute.node_set.dm import models as node_set_models
from exordos_core.tests.functional.restapi.network.test_sdn_contract_api import (
    collection,
)
from exordos_core.tests.functional.restapi.network.test_sdn_contract_api import create
from exordos_core.tests.functional.restapi.network.test_sdn_contract_api import (
    node_ports,
)
from exordos_core.tests.functional.restapi.network.test_sdn_contract_api import resource
from exordos_core.user_api.iam.dm import models as iam
from exordos_core.user_api.network.dm import models as catalog


@pytest.fixture
def client(user_api_client, auth_user_admin, user_api):
    yield user_api_client(auth_user_admin)
    user_api._restore_seed_snapshot()


def guest(client, project_id, **kwargs):
    return create(
        client,
        ("compute", "nodes"),
        project_id=str(project_id),
        cores=1,
        ram=1024,
        disk_spec={"kind": "root_disk", "image": "ubuntu_24.04"},
        **kwargs,
    )


def pool(client, cidr="198.51.100.0/28", **kwargs):
    network = create(
        client,
        ("network", "networks"),
        driver_spec={"driver": "ovs_evpn"},
        access="public",
    )
    return create(
        client,
        ("network", "subnets"),
        network=network["uuid"],
        cidr=cidr,
        floating_pool=True,
        placeable=False,
        **kwargs,
    )


def test_node_exposes_primary_network_without_reverse_ports(client):
    node = guest(client, uuid.uuid4())
    assert "ports" not in node
    ports = client.get(
        collection(client, "network", "ports"), params={"node": node["uuid"]}
    ).json()
    assert len(ports) == 1
    assert node["default_network"]["port"] == ports[0]["uuid"]
    assert "floating_ip" not in node["default_network"]


def test_node_reports_automatic_fip_in_default_network(client):
    public = pool(client, default_floating_pool=True)
    node = guest(client, uuid.uuid4(), default_network={"auto_floating_ip": True})
    address = catalog.Address.objects.get_one(
        filters={"port": filters.EQ(node["default_network"]["port"])}
    )
    assert str(address.subnet) == public["uuid"]
    assert node["default_network"]["floating_ip"] == str(address.address)
    response = client.get(resource(client, "compute", "nodes", node["uuid"])).json()
    assert response["default_network"] == node["default_network"]
    stored = compute.Node.objects.get_one(filters={"uuid": filters.EQ(node["uuid"])})
    compute.Node.project_observations([stored])
    assert stored.dump_to_simple_view()["default_network"] == node["default_network"]
    assert "floating_ip" not in json.loads(
        stored._get_prepared_data()["default_network"]
    )


@pytest.mark.parametrize("published", [False, True])
def test_node_fip_tracks_independent_address_binding_and_deletion(client, published):
    public = pool(client)
    node = guest(client, uuid.uuid4())
    node_url = resource(client, "compute", "nodes", node["uuid"])
    address = create(
        client,
        ("network", "catalog", "addresses"),
        project_id=node["project_id"],
        kind="floating",
        subnet=public["uuid"],
    )
    address_url = resource(client, "network", "catalog", "addresses", address["uuid"])
    for _ in range(2):
        client.put(address_url, json={"port": node["default_network"]["port"]})
        assert client.get(node_url).json()["default_network"] == {
            **node["default_network"],
            "floating_ip": address["address"],
        }
        client.put(address_url, json={"port": None})
        assert client.get(node_url).json()["default_network"] == node["default_network"]
    client.put(address_url, json={"port": node["default_network"]["port"]})
    if published:
        stored = catalog.Address.objects.get_one(
            filters={"uuid": filters.EQ(address["uuid"])}
        )
        stored.claim_publication(uuid.UUID(node["default_network"]["port"]))
    client.delete(address_url)
    assert client.get(node_url).json()["default_network"] == node["default_network"]


def test_secondary_fip_is_not_the_node_default_fip(client):
    public = pool(client)
    node = guest(client, uuid.uuid4())
    secondary = create(
        client,
        ("network", "ports"),
        project_id=node["project_id"],
        node=node["uuid"],
    )
    create(
        client,
        ("network", "catalog", "addresses"),
        project_id=node["project_id"],
        kind="floating",
        subnet=public["uuid"],
        port=secondary["uuid"],
    )
    response = client.get(resource(client, "compute", "nodes", node["uuid"])).json()
    assert response["default_network"] == node["default_network"]


@pytest.mark.parametrize("automatic", [False, True])
def test_hw_node_does_not_report_an_unallocated_fip(client, automatic):
    public = pool(client)
    node = guest(
        client,
        uuid.uuid4(),
        node_type="HW",
        default_network={
            "auto_floating_ip": True,
            **({} if automatic else {"floating_network": public["network"]}),
        },
    )
    assert "floating_ip" not in node["default_network"]
    response = client.get(resource(client, "compute", "nodes", node["uuid"])).json()
    assert "floating_ip" not in response["default_network"]


def test_node_and_port_share_lazy_default_without_iam_project(client):
    project_id = uuid.uuid4()
    assert (
        iam.Project.objects.get_one_or_none(filters={"uuid": filters.EQ(project_id)})
        is None
    )
    node = guest(client, project_id)
    assert len(node_ports(client, node)) == 1
    port = node_ports(client, node)[0]
    assert node["default_network"]["ipv4"] == port["ipv4"] == "10.128.0.2"
    assert port["index"] == 0
    network = client.get(
        resource(client, "network", "networks", port["network"])
    ).json()
    assert network["is_default"] is True
    subnet = client.get(resource(client, "network", "subnets", port["subnet"])).json()
    assert subnet["cidr"] == "10.128.0.0/20"
    second = create(client, ("network", "ports"), project_id=str(project_id))
    assert second["network"] == port["network"]
    assert second["ipv4"] == "10.128.0.3"
    assert (
        iam.Project.objects.get_one_or_none(filters={"uuid": filters.EQ(project_id)})
        is None
    )


def test_empty_template_creates_no_network_resources(client):
    project_id = uuid.uuid4()
    node = guest(client, project_id, default_network={})
    assert node_ports(client, node) == [] and node["default_network"] == {}
    assert (
        compute.Network.objects.get_all(filters={"project_id": filters.EQ(project_id)})
        == []
    )


@pytest.mark.parametrize(
    "kind, permission",
    [("nodes", "compute.node.create"), ("sets", "compute.node_set.create")],
)
def test_default_port_only_requires_compute_creation(
    client, user_api_client, auth_test1_user, kind, permission
):
    creator = user_api_client(auth_test1_user, permissions=[permission])
    body = create(
        creator,
        ("compute", kind),
        cores=1,
        ram=1024,
        disk_spec={"kind": "root_disk", "image": "ubuntu_24.04"},
        **({"replicas": 1} if kind == "sets" else {}),
    )
    if kind == "sets":
        nodeset = node_set_models.NodeSet.objects.get_one(
            filters={"uuid": filters.EQ(body["uuid"])}
        )
        node = nodeset.gen_nodes(project_id=nc.NODE_SET_PROJECT)[0]
        node.insert()
        assert len(node_ports(client, node)) == 1
    else:
        assert len(node_ports(client, body)) == 1
    with pytest.raises(exceptions.ForbiddenError):
        create(creator, ("network", "ports"))


def test_primary_port_network_override_only_requires_node_creation(
    client, user_api_client, auth_test1_user
):
    private = create(
        client, ("network", "networks"), driver_spec={"driver": "ovs_evpn"}
    )
    create(client, ("network", "subnets"), network=private["uuid"], cidr="10.42.0.0/24")
    creator = user_api_client(auth_test1_user, permissions=["compute.node.create"])
    node = guest(
        creator, uuid.UUID(int=0), default_network={"network": private["uuid"]}
    )
    assert node_ports(client, node)[0]["network"] == private["uuid"]
    with pytest.raises(exceptions.ForbiddenError):
        create(creator, ("network", "ports"), network=private["uuid"])


def test_automatic_fip_still_requires_address_creation(
    client, user_api_client, auth_test1_user
):
    pool(client, default_floating_pool=True)
    creator = user_api_client(auth_test1_user, permissions=["compute.node.create"])
    with pytest.raises(exceptions.ForbiddenError):
        guest(creator, uuid.UUID(int=0), default_network={"auto_floating_ip": True})
    without_fip = guest(
        creator, uuid.UUID(int=0), default_network={"auto_floating_ip": False}
    )
    assert "floating_ip" not in without_fip["default_network"]
    with_address = user_api_client(
        auth_test1_user, permissions=["network.address.create"]
    )
    node = guest(
        with_address, uuid.UUID(int=0), default_network={"auto_floating_ip": True}
    )
    assert len(node_ports(client, node)) == 1


def test_default_is_independent_of_display_name(client):
    project_id = uuid.uuid4()
    other = create(
        client,
        ("network", "networks"),
        project_id=str(project_id),
        name="default",
        driver_spec={"driver": "ovs_evpn"},
    )
    port = create(client, ("network", "ports"), project_id=str(project_id))
    assert port["network"] != other["uuid"]


def legacy_flat_port(project_id, cidr="10.20.0.0/24", **kwargs):
    # Old generated VM ports may use the operator's private flat subnet.
    network = compute.Network(
        project_id=uuid.UUID(int=0),
        driver_spec={"driver": "flat_bridge", "dhcp_cfg": "/tmp/default-network-dhcp"},
    )
    network.insert()
    subnet = compute.Subnet.restore_from_simple_view(
        project_id=str(network.project_id),
        network=str(network.uuid),
        name="flat",
        cidr=cidr,
        **kwargs,
    )
    subnet.insert()
    # Seed the cross-project placement that released Core generated.
    port = compute.Port(
        project_id=project_id,
        subnet=subnet.uuid,
        ipv4=subnet.cidr[2],
        mask=subnet.cidr.netmask,
        mac=compute.Port.generate_mac(),
    )
    orm.SQLStorableMixin.insert(port)
    return port


@pytest.mark.parametrize("template", [None, {}])
def test_legacy_project_reuses_flat_for_node_and_standalone_port(client, template):
    project_id = uuid.uuid4()
    previous = legacy_flat_port(project_id)
    node = guest(
        client,
        project_id,
        **({"default_network": template} if template is not None else {}),
    )
    port = create(client, ("network", "ports"), project_id=str(project_id))
    assert (
        node_ports(client, node)[0]["subnet"] == port["subnet"] == str(previous.subnet)
    )
    assert node["default_network"]["ipv4"] == "10.20.0.3"
    assert "legacy_flat_placement" not in port
    assert (
        compute.Network.objects.get_all(
            filters={
                "project_id": filters.EQ(project_id),
                "is_default": filters.EQ(True),
            }
        )
        == []
    )
    # Existing placements continue to work after disabling new flat defaults.
    config.CONF.set_override("legacy_flat_default", False, group="network")
    try:
        assert (
            client.put(
                resource(client, "network", "ports", port["uuid"]),
                json={"name": "retained"},
            ).status_code
            == 200
        )
    finally:
        config.CONF.clear_override("legacy_flat_default", group="network")


def test_legacy_default_does_not_bypass_node_ownership(client):
    project_id = uuid.uuid4()
    legacy_flat_port(project_id)
    foreign = guest(client, uuid.uuid4(), default_network={})
    with pytest.raises(exceptions.BadRequestError):
        create(
            client,
            ("network", "ports"),
            project_id=str(project_id),
            node=foreign["uuid"],
        )


@pytest.mark.parametrize("standalone", [False, True])
def test_implicit_foreign_flat_ip_requires_explicit_address_permission(
    client, user_api_client, auth_test1_p1_user, standalone
):
    project_id = uuid.UUID(auth_test1_p1_user.project_id)
    legacy_flat_port(project_id)
    permission = "network.port.create" if standalone else "compute.node.create"
    creator = user_api_client(
        auth_test1_p1_user, permissions=[permission], project_id=str(project_id)
    )

    def allocate(caller):
        if standalone:
            return create(
                caller,
                ("network", "ports"),
                project_id=str(project_id),
                target_ipv4="10.20.0.50",
            )
        return guest(caller, project_id, default_network={"target_ipv4": "10.20.0.50"})

    with pytest.raises(exceptions.ForbiddenError):
        allocate(creator)
    pinner = user_api_client(
        auth_test1_p1_user,
        permissions=["network.address.explicit_address"],
        project_id=str(project_id),
    )
    created = allocate(pinner)
    assert (
        created["ipv4"] if standalone else node_ports(client, created)[0]["ipv4"]
    ) == "10.20.0.50"


def test_fip_private_ip_uses_owned_sdn_without_foreign_flat_permission(
    client, user_api_client, auth_test1_p1_user
):
    project_id = uuid.UUID(auth_test1_p1_user.project_id)
    legacy_flat_port(project_id)
    pool(client, default_floating_pool=True)
    creator = user_api_client(
        auth_test1_p1_user,
        permissions=["compute.node.create", "network.address.create"],
        project_id=str(project_id),
    )
    node = guest(
        creator,
        project_id,
        default_network={"target_ipv4": "10.128.0.50", "auto_floating_ip": True},
    )
    assert node_ports(client, node)[0]["ipv4"] == "10.128.0.50"


@pytest.mark.parametrize("standalone", [False, True])
def test_concurrent_first_flat_port_cannot_bypass_address_permission(
    client, user_api_client, auth_test1_p1_user, monkeypatch, standalone
):
    from exordos_core.network import permissions

    project_id = uuid.UUID(auth_test1_p1_user.project_id)
    permission = "network.port.create" if standalone else "compute.node.create"
    creator = user_api_client(
        auth_test1_p1_user, permissions=[permission], project_id=str(project_id)
    )
    original = permissions.gate_explicit_address
    seeded = []

    def interleave(*args, **kwargs):
        original(*args, **kwargs)
        if not seeded:
            # A legacy reconciler commits the project's first flat port after preflight.
            with ThreadPoolExecutor(max_workers=1) as worker:
                seeded.append(worker.submit(legacy_flat_port, project_id).result())

    monkeypatch.setattr(permissions, "gate_explicit_address", interleave)
    identifier = uuid.uuid4()
    with pytest.raises(exceptions.ForbiddenError):
        if standalone:
            create(
                creator,
                ("network", "ports"),
                uuid=str(identifier),
                project_id=str(project_id),
                target_ipv4="10.20.0.50",
            )
        else:
            guest(
                creator,
                project_id,
                uuid=str(identifier),
                default_network={"target_ipv4": "10.20.0.50"},
            )
    model = compute.Port if standalone else compute.Node
    assert (
        model.objects.get_one_or_none(filters={"uuid": filters.EQ(identifier)}) is None
    )
    ports = compute.Port.objects.get_all(filters={"project_id": filters.EQ(project_id)})
    assert [port.uuid for port in ports] == [seeded[0].uuid]
    assert (
        compute.Volume.objects.get_all(filters={"project_id": filters.EQ(project_id)})
        == []
    )


def test_legacy_mode_disabled_uses_sdn_even_with_flat_ports(client):
    project_id = uuid.uuid4()
    previous = legacy_flat_port(project_id)
    config.CONF.set_override("legacy_flat_default", False, group="network")
    try:
        node = guest(client, project_id)
        assert node_ports(client, node)[0]["subnet"] != str(previous.subnet)
        assert node["default_network"]["ipv4"] == "10.128.0.2"
    finally:
        config.CONF.clear_override("legacy_flat_default", group="network")


def test_legacy_ports_in_another_project_do_not_change_default(client):
    legacy_flat_port(uuid.uuid4())
    node = guest(client, uuid.uuid4())
    assert node["default_network"]["ipv4"] == "10.128.0.2"


@pytest.mark.parametrize("template", [None, {}])
def test_legacy_nodeset_new_replica_stays_on_flat(client, template):
    project_id = uuid.uuid4()
    previous = legacy_flat_port(project_id)
    body = create(
        client,
        ("compute", "sets"),
        project_id=str(project_id),
        replicas=1,
        cores=1,
        ram=1024,
        disk_spec={"kind": "root_disk", "image": "ubuntu_24.04"},
        **({"default_network": template} if template is not None else {}),
    )
    nodeset = node_set_models.NodeSet.objects.get_one(
        filters={"uuid": filters.EQ(body["uuid"])}
    )
    node = nodeset.gen_nodes(project_id=nc.NODE_SET_PROJECT)[0]
    node.insert()
    assert node_ports(client, node)[0]["subnet"] == str(previous.subnet)


@pytest.mark.parametrize("automatic_pool", [True, False])
def test_fip_without_private_selector_bypasses_flat_compatibility(
    client, automatic_pool
):
    project_id = uuid.uuid4()
    previous = legacy_flat_port(project_id)
    legacy_flat_port(project_id, "10.21.0.0/24")
    public = pool(client, default_floating_pool=True)
    node = guest(
        client,
        project_id,
        default_network={
            "auto_floating_ip": True,
            **({} if automatic_pool else {"floating_network": public["network"]}),
        },
    )
    port = node_ports(client, node)[0]
    network = compute.Network.objects.get_one(
        filters={"uuid": filters.EQ(port["network"])}
    )
    assert network.project_id == project_id
    assert network.is_default and network.driver_spec["driver"] == "ovs_evpn"
    assert node["default_network"]["ipv4"] == "10.128.0.2"
    address = catalog.Address.objects.get_one(
        filters={"port": filters.EQ(port["uuid"])}
    )
    assert str(address.subnet) == public["uuid"]
    retained = compute.Port.objects.get_one(filters={"uuid": filters.EQ(previous.uuid)})
    assert retained.subnet == previous.subnet and retained.ipv4 == previous.ipv4


def test_disabled_automatic_fip_preserves_flat_placement(client):
    project_id = uuid.uuid4()
    previous = legacy_flat_port(project_id)
    node = guest(client, project_id, default_network={"auto_floating_ip": False})
    assert node_ports(client, node)[0]["subnet"] == str(previous.subnet)
    assert "floating_ip" not in node["default_network"]
    assert (
        catalog.Address.objects.get_all(filters={"project_id": filters.EQ(project_id)})
        == []
    )


def test_explicit_placement_overrides_flat_compatibility(client):
    project_id = uuid.uuid4()
    legacy_flat_port(project_id)
    network = create(
        client,
        ("network", "networks"),
        project_id=str(project_id),
        driver_spec={"driver": "ovs_evpn"},
    )
    create(
        client,
        ("network", "subnets"),
        project_id=str(project_id),
        network=network["uuid"],
        cidr="10.42.0.0/24",
    )
    node = guest(client, project_id, default_network={"network": network["uuid"]})
    assert node["default_network"]["ipv4"] == "10.42.0.2"
    empty = guest(client, project_id, default_network={})
    assert empty["default_network"]["ipv4"] == "10.20.0.3"


def test_empty_template_suppresses_port_when_flat_compatibility_is_disabled(client):
    project_id = uuid.uuid4()
    legacy_flat_port(project_id)
    config.CONF.set_override("legacy_flat_default", False, group="network")
    try:
        node = guest(client, project_id, default_network={})
        assert node_ports(client, node) == []
        assert (
            compute.Network.objects.get_all(
                filters={"project_id": filters.EQ(project_id)}
            )
            == []
        )
    finally:
        config.CONF.clear_override("legacy_flat_default", group="network")


@pytest.mark.parametrize("path", ["nodes", "sets"])
@pytest.mark.parametrize("value", [None, [], [{"port": {}}]])
def test_networking_is_no_longer_a_creation_field(client, path, value):
    with pytest.raises(exceptions.BadRequestError):
        create(
            client,
            ("compute", path),
            cores=1,
            ram=1024,
            disk_spec={"kind": "root_disk", "image": "ubuntu_24.04"},
            networking=value,
        )


@pytest.mark.parametrize("template", [None, {}])
def test_multiple_legacy_flat_subnets_require_explicit_placement(client, template):
    project_id = uuid.uuid4()
    legacy_flat_port(project_id)
    legacy_flat_port(project_id, "10.21.0.0/24")
    with pytest.raises(exceptions.ConflictError):
        guest(
            client,
            project_id,
            **({"default_network": template} if template is not None else {}),
        )


def test_address_only_flat_pool_does_not_select_vm_network(client):
    legacy_flat_port(uuid.UUID(int=0), placeable=False)
    node = guest(client, uuid.UUID(int=0))
    assert node["default_network"]["ipv4"] == "10.128.0.2"


def test_bootstrap_manual_ports_do_not_get_an_extra_autoport(client, monkeypatch):
    from exordos_core.bootstrap import defaults
    from exordos_core.common import constants as cc

    legacy_flat_port(cc.EM_PROJECT_ID)
    network = compute.Network(
        project_id=uuid.UUID(int=0),
        driver_spec={"driver": "flat_bridge", "dhcp_cfg": "/tmp/bootstrap-dhcp"},
    )
    network.insert()
    subnet = compute.Subnet.restore_from_simple_view(
        uuid=str(cc.MAIN_SUBNET_UUID),
        project_id=str(network.project_id),
        network=str(network.uuid),
        cidr="10.20.0.0/24",
    )
    subnet.insert()
    monkeypatch.setattr(defaults, "seed_core_set_target", lambda _: None)
    node_uuid = uuid.uuid4()
    spec = {
        "stand": {
            "network": {"name": "flat"},
            "bootstraps": [
                {
                    "uuid": str(node_uuid),
                    "cores": 1,
                    "memory": 1024,
                    "image_uri": "ubuntu_24.04",
                    "disks": [{"size": 10, "label": "root"}],
                    "ports": [{"ip": "10.20.0.2", "mac": "52:54:00:01:02:03"}],
                }
            ],
        }
    }
    defaults.add_core_set(spec)
    node = compute.Node.objects.get_one(filters={"uuid": filters.EQ(node_uuid)})
    assert len(node_ports(client, node)) == 1
    assert node_ports(client, node)[0]["index"] == 0
    assert node_ports(client, node)[0]["ipv4"] == "10.20.0.2"
    assert node_ports(client, node)[0]["subnet"] == str(subnet.uuid)


def test_explicit_missing_network_does_not_create_default(client):
    project_id = uuid.uuid4()
    with pytest.raises(exceptions.BadRequestError):
        guest(client, project_id, default_network={"network": str(uuid.uuid4())})
    assert (
        compute.Network.objects.get_all(filters={"project_id": filters.EQ(project_id)})
        == []
    )
    assert (
        compute.Node.objects.get_all(filters={"project_id": filters.EQ(project_id)})
        == []
    )


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {"floating_ip": False},
        {"floating_ip": {}},
        {"floating_ip": True},
        {"floating_ip": "198.51.100.5"},
        {"auto_floating_ip": None},
        {"auto_floating_ip": 1},
        {"auto_floating_ip": "true"},
        {"floating_network": str(uuid.uuid4())},
        {"auto_floating_ip": False, "floating_network": str(uuid.uuid4())},
        {"auto_floating_ip": True, "floating_network": None},
        {"auto_floating_ip": True, "floating_network": "not-a-uuid"},
        {"ipv4": "10.128.0.2"},
        {"network": None},
        {"network": str(uuid.uuid4()), "subnet": str(uuid.uuid4())},
    ],
)
@pytest.mark.parametrize("kind", ["nodes", "sets"])
def test_invalid_template_is_rejected_before_creation(client, value, kind):
    project_id = uuid.uuid4()
    with pytest.raises(exceptions.BadRequestError):
        if kind == "nodes":
            guest(client, project_id, default_network=value)
        else:
            create(
                client,
                ("compute", "sets"),
                project_id=str(project_id),
                cores=1,
                ram=1024,
                replicas=1,
                disk_spec={"kind": "root_disk", "image": "ubuntu_24.04"},
                default_network=value,
            )
    assert (
        compute.Node.objects.get_all(filters={"project_id": filters.EQ(project_id)})
        == []
    )


def test_automatic_fip_uses_marked_default_pool(client):
    pool(client)
    preferred = pool(client, "203.0.113.0/28", default_floating_pool=True)
    project_id = uuid.uuid4()
    node = guest(client, project_id, default_network={"auto_floating_ip": True})
    addresses = client.get(
        collection(client, "network", "catalog", "addresses"),
        params={"port": node_ports(client, node)[0]["uuid"]},
    ).json()
    assert len(addresses) == 1
    assert addresses[0]["subnet"] == preferred["uuid"]
    assert addresses[0]["project_id"] == str(project_id)


def test_automatic_fip_uses_only_available_pool(client):
    available = pool(client)
    node = guest(client, uuid.uuid4(), default_network={"auto_floating_ip": True})
    address = catalog.Address.objects.get_one(
        filters={"port": filters.EQ(node_ports(client, node)[0]["uuid"])}
    )
    assert str(address.subnet) == available["uuid"]


def test_marked_inaccessible_pool_is_not_used(client):
    private = create(
        client, ("network", "networks"), driver_spec={"driver": "ovs_evpn"}
    )
    create(
        client,
        ("network", "subnets"),
        network=private["uuid"],
        cidr="203.0.113.0/28",
        floating_pool=True,
        default_floating_pool=True,
        placeable=False,
    )
    available = pool(client)
    node = guest(client, uuid.uuid4(), default_network={"auto_floating_ip": True})
    address = catalog.Address.objects.get_one(
        filters={"port": filters.EQ(node_ports(client, node)[0]["uuid"])}
    )
    assert str(address.subnet) == available["uuid"]


def test_exhausted_fip_pool_rolls_back_private_resources(client):
    public = pool(client, ip_range="198.51.100.2-198.51.100.2")
    create(
        client,
        ("network", "catalog", "addresses"),
        kind="floating",
        subnet=public["uuid"],
    )
    project_id = uuid.uuid4()
    with pytest.raises(exceptions.BadRequestError):
        guest(client, project_id, default_network={"auto_floating_ip": True})
    for model in (compute.Node, compute.Port, compute.Network, compute.Volume):
        assert (
            model.objects.get_all(filters={"project_id": filters.EQ(project_id)}) == []
        )


def test_default_floating_pool_requires_operator_permission(
    client, user_api_client, auth_test1_user
):
    public = pool(client)
    tenant = user_api_client(auth_test1_user, permissions=["network.subnet.update"])
    with pytest.raises(exceptions.ForbiddenError):
        tenant.put(
            resource(tenant, "network", "subnets", public["uuid"]),
            json={"default_floating_pool": True},
        )
    assert (
        client.put(
            resource(client, "network", "subnets", public["uuid"]),
            json={"default_floating_pool": True},
        ).json()["default_floating_pool"]
        is True
    )
    with pytest.raises(exceptions.ForbiddenError):
        tenant.put(
            resource(tenant, "network", "subnets", public["uuid"]),
            json={"default_floating_pool": False},
        )


def test_only_one_marked_pool_per_owner_project(client):
    pool(client, default_floating_pool=True)
    with pytest.raises(exceptions.ConflictError):
        pool(client, "203.0.113.0/28", default_floating_pool=True)


@pytest.mark.parametrize(
    "template", [{}, {"auto_floating_ip": True}, {"network": str(uuid.uuid4())}]
)
def test_node_template_updates_without_changing_existing_networking(client, template):
    project = uuid.uuid4()
    node = guest(client, project)
    ports = node_ports(client, node)
    updated = client.put(
        resource(client, "compute", "nodes", node["uuid"]),
        json={"default_network": template},
    ).json()
    stored = compute.Node.objects.get_one(filters={"uuid": filters.EQ(node["uuid"])})
    assert stored.properties.get_value("default_network") == template
    assert node_ports(client, node) == ports
    assert updated["default_network"]["port"] == node["default_network"]["port"]
    assert updated["default_network"]["ipv4"] == node["default_network"]["ipv4"]
    assert (
        catalog.Address.objects.get_all(
            filters={"project_id": filters.EQ(project), "kind": filters.EQ("floating")}
        )
        == []
    )
    assert "default_network" not in stored.to_ua_resource().value


def test_removing_last_port_does_not_create_another(client):
    from exordos_core.network.service import NetworkService

    node = guest(client, uuid.uuid4())
    client.delete(
        resource(client, "network", "ports", node_ports(client, node)[0]["uuid"])
    )
    stored = compute.Node.objects.get_one(filters={"uuid": filters.EQ(node["uuid"])})
    NetworkService()._iteration()
    compute.Node.project_observations([stored])
    assert node_ports(client, stored) == [] and stored.default_network == {}


@pytest.mark.parametrize("multiple", [False, True])
def test_missing_or_ambiguous_pool_rolls_back(client, multiple):
    if multiple:
        pool(client)
        pool(client, "203.0.113.0/28")
    project_id = uuid.uuid4()
    expected = exceptions.ConflictError if multiple else exceptions.BadRequestError
    with pytest.raises(expected):
        guest(client, project_id, default_network={"auto_floating_ip": True})
    assert (
        compute.Node.objects.get_all(filters={"project_id": filters.EQ(project_id)})
        == []
    )
    assert (
        compute.Network.objects.get_all(filters={"project_id": filters.EQ(project_id)})
        == []
    )


@pytest.mark.parametrize("selector", ["network", "subnet"])
def test_explicit_network_and_fip_pool(client, selector):
    legacy_flat_port(uuid.UUID(int=0))
    private = create(
        client, ("network", "networks"), driver_spec={"driver": "ovs_evpn"}
    )
    subnet = create(
        client, ("network", "subnets"), network=private["uuid"], cidr="10.42.0.0/24"
    )
    public = pool(client)
    pool(client, cidr="203.0.113.0/28", default_floating_pool=True)
    node = guest(
        client,
        uuid.UUID(int=0),
        default_network={
            selector: private["uuid"] if selector == "network" else subnet["uuid"],
            "auto_floating_ip": True,
            "floating_network": public["network"],
        },
    )
    assert node_ports(client, node)[0]["subnet"] == subnet["uuid"]
    assert node["default_network"]["ipv4"] == "10.42.0.2"
    address = catalog.Address.objects.get_one(
        filters={"port": filters.EQ(node["default_network"]["port"])}
    )
    assert str(address.subnet) == public["uuid"]


@pytest.mark.parametrize("ambiguous", [False, True])
def test_explicit_floating_network_does_not_fall_back_to_default_pool(
    client, ambiguous
):
    pool(client, default_floating_pool=True)
    floating_network = str(uuid.uuid4())
    if ambiguous:
        public = pool(client, cidr="203.0.113.0/28")
        floating_network = public["network"]
        create(
            client,
            ("network", "subnets"),
            network=floating_network,
            cidr="203.0.113.16/28",
            floating_pool=True,
            placeable=False,
        )
    project_id = uuid.uuid4()
    error = exceptions.ConflictError if ambiguous else exceptions.BadRequestError
    with pytest.raises(error):
        guest(
            client,
            project_id,
            default_network={
                "auto_floating_ip": True,
                "floating_network": floating_network,
            },
        )
    for model in (compute.Node, compute.Port, compute.Network, catalog.Address):
        assert (
            model.objects.get_all(filters={"project_id": filters.EQ(project_id)}) == []
        )


@pytest.mark.parametrize("automatic_pool", [True, False])
def test_nodeset_allocates_distinct_ports_and_fips_in_owner_project(
    client, automatic_pool
):
    public = pool(client, default_floating_pool=True)
    project_id = uuid.uuid4()
    previous = legacy_flat_port(project_id)
    body = create(
        client,
        ("compute", "sets"),
        project_id=str(project_id),
        replicas=2,
        cores=1,
        ram=1024,
        disk_spec={"kind": "root_disk", "image": "ubuntu_24.04"},
        default_network={
            "auto_floating_ip": True,
            **({} if automatic_pool else {"floating_network": public["network"]}),
        },
    )
    nodeset = node_set_models.NodeSet.objects.get_one(
        filters={"uuid": filters.EQ(body["uuid"])}
    )
    nodes = nodeset.gen_nodes(project_id=nc.NODE_SET_PROJECT)
    for node in nodes:
        assert node.to_ua_resource().value["default_network"] == body["default_network"]
        node.insert()
    ports = compute.Port.objects.get_all(
        filters={
            "project_id": filters.EQ(project_id),
            "uuid": filters.NE(previous.uuid),
        }
    )
    assert len(ports) == 2 and ports[0].ipv4 != ports[1].ipv4
    network = compute.Network.objects.get_one(
        filters={"project_id": filters.EQ(project_id), "is_default": filters.EQ(True)}
    )
    assert network.driver_spec["driver"] == "ovs_evpn"
    assert all(str(port.subnet) != str(previous.subnet) for port in ports)
    addresses = catalog.Address.objects.get_all(
        filters={"project_id": filters.EQ(project_id)}
    )
    assert len(addresses) == 2 and addresses[0].address != addresses[1].address
    assert all(str(address.subnet) == public["uuid"] for address in addresses)


def test_concurrent_first_ports_share_one_default_network(client):
    project_id = uuid.uuid4()

    # Model inserts use separate transaction contexts in each worker.
    def insert(_):
        port = compute.Port(project_id=project_id)
        port.insert()
        return port

    with ThreadPoolExecutor(max_workers=4) as workers:
        ports = list(workers.map(insert, range(4)))
    networks = compute.Network.objects.get_all(
        filters={"project_id": filters.EQ(project_id), "is_default": filters.EQ(True)}
    )
    assert len(networks) == 1
    assert len({str(port.subnet) for port in ports}) == 1
    assert len({str(port.ipv4) for port in ports}) == 4


@pytest.mark.parametrize("template", [None, {}])
def test_nodeset_preserves_omitted_vs_empty_template(client, template):
    project_id = uuid.uuid4()
    body = create(
        client,
        ("compute", "sets"),
        project_id=str(project_id),
        replicas=1,
        cores=1,
        ram=1024,
        disk_spec={"kind": "root_disk", "image": "ubuntu_24.04"},
        **({"default_network": template} if template is not None else {}),
    )
    nodeset = node_set_models.NodeSet.objects.get_one(
        filters={"uuid": filters.EQ(body["uuid"])}
    )
    node = nodeset.gen_nodes(project_id=nc.NODE_SET_PROJECT)[0]
    node.insert()
    assert len(node_ports(client, node)) == (1 if template is None else 0)


def test_nodeset_template_update_only_affects_new_replicas(client):
    body = create(
        client,
        ("compute", "sets"),
        replicas=1,
        cores=1,
        ram=1024,
        disk_spec={"kind": "root_disk", "image": "ubuntu_24.04"},
    )
    nodeset = node_set_models.NodeSet.objects.get_one(
        filters={"uuid": filters.EQ(body["uuid"])}
    )
    original = nodeset.gen_nodes(project_id=nc.NODE_SET_PROJECT)[0]
    original.insert()
    port_id = node_ports(client, original)[0]["uuid"]
    client.put(
        resource(client, "compute", "sets", body["uuid"]),
        json={"default_network": {}, "replicas": 2},
    )
    nodeset = node_set_models.NodeSet.objects.get_one(
        filters={"uuid": filters.EQ(body["uuid"])}
    )
    fresh = nodeset.gen_nodes(
        project_id=nc.NODE_SET_PROJECT, node_uuids=[original.uuid]
    )[1]
    fresh.insert()
    assert node_ports(client, fresh) == []
    assert (
        node_ports(
            client,
            compute.Node.objects.get_one(filters={"uuid": filters.EQ(original.uuid)}),
        )[0]["uuid"]
        == port_id
    )


def test_manifest_reapply_does_not_recreate_port_or_fip(client, tmp_path):
    from gcl_sdk.agents.universal.clients.backend.db import DatabaseBackendClient
    from gcl_sdk.agents.universal.clients.backend.db import ModelSpec
    from gcl_sdk.agents.universal.drivers.direct import DirectAgentDriver
    from gcl_sdk.agents.universal.storage.fs import TargetFieldsFileStorage

    from exordos_core.elements.dm import models as em

    pool(client, default_floating_pool=True)
    core = em.Manifest(name="core", version="1.0.0", resources={})
    core.save()
    core.install()
    project_id = uuid.uuid4()
    manifest = em.Manifest(
        name="automatic",
        version="1.0.0",
        requirements={"core": {"from_version": "0.0.0"}},
        resources={
            "$core.compute.nodes": {
                "vm": {
                    "project_id": str(project_id),
                    "cores": 1,
                    "ram": 1024,
                    "disk_spec": {"kind": "root_disk", "image": "ubuntu_24.04"},
                    "default_network": {"auto_floating_ip": True},
                }
            }
        },
    )
    manifest.save()
    manifest.validate()
    manifest.install()
    item = next(
        item
        for item in em.element_engine.get_resources()
        if isinstance(item, em.Resource) and item.element.name == "automatic"
    )
    storage = TargetFieldsFileStorage(str(tmp_path / "target-fields.json"))
    backend = DatabaseBackendClient(
        [
            ModelSpec(
                compute.Node,
                item.kind,
                filters={},
            )
        ],
        storage,
    )

    class Driver(DirectAgentDriver):
        def get_capabilities(self):
            return [item.kind]

    driver = Driver(backend, storage)
    item.actualize()
    actual = driver.create(item.target_resource)
    assert actual.hash == item.target_resource.hash
    actual.insert()
    ports = compute.Port.objects.get_all(filters={"project_id": filters.EQ(project_id)})
    addresses = catalog.Address.objects.get_all(
        filters={"project_id": filters.EQ(project_id)}
    )
    original = ([port.uuid for port in ports], [address.uuid for address in addresses])
    for _ in range(2):
        item.actualize()
        assert driver.update(item.target_resource).hash == item.target_resource.hash
    # Updating the stored template does not replay Port/FIP creation.
    item.value = {**item.value, "default_network": {}}
    item.update()
    item.actualize()
    assert driver.update(item.target_resource).hash == item.target_resource.hash
    stored = compute.Node.objects.get_one(filters={"uuid": filters.EQ(item.uuid)})
    assert stored.properties.get_value("default_network") == {}
    compute.Node.project_observations([stored])
    assert stored.default_network["floating_ip"] == str(addresses[0].address)
    assert [
        port.uuid
        for port in compute.Port.objects.get_all(
            filters={"project_id": filters.EQ(project_id)}
        )
    ] == original[0]
    assert [
        address.uuid
        for address in catalog.Address.objects.get_all(
            filters={"project_id": filters.EQ(project_id)}
        )
    ] == original[1]


@pytest.mark.parametrize("kind", ["nodes", "sets"])
@pytest.mark.parametrize(
    "value", [None, [], [{"kind": "subnet", "network": str(uuid.uuid4())}]]
)
def test_interfaces_is_rejected_on_create_and_update(client, kind, value):
    body = dict(
        cores=1,
        ram=1024,
        disk_spec={"kind": "root_disk", "image": "ubuntu_24.04"},
        default_network={},
        **({"replicas": 1} if kind == "sets" else {}),
    )
    with pytest.raises(exceptions.BadRequestError):
        create(client, ("compute", kind), **body, interfaces=value)
    saved = create(client, ("compute", kind), **body)
    assert "interfaces" not in saved
    with pytest.raises(exceptions.BadRequestError):
        client.put(
            resource(client, "compute", kind, saved["uuid"]), json={"interfaces": value}
        )


def test_nodeset_replica_template_round_trips_without_changing_its_port(
    client, tmp_path
):
    from gcl_sdk.agents.universal.clients.backend.db import DatabaseBackendClient
    from gcl_sdk.agents.universal.clients.backend.db import ModelSpec
    from gcl_sdk.agents.universal.drivers.direct import DirectAgentDriver
    from gcl_sdk.agents.universal.storage.fs import TargetFieldsFileStorage

    pool(client, default_floating_pool=True)
    template = {"auto_floating_ip": True}
    body = create(
        client,
        ("compute", "sets"),
        project_id=str(uuid.uuid4()),
        replicas=1,
        cores=1,
        ram=1024,
        disk_spec={"kind": "root_disk", "image": "ubuntu_24.04"},
        default_network=template,
    )
    nodeset = node_set_models.NodeSet.objects.get_one(
        filters={"uuid": filters.EQ(body["uuid"])}
    )
    replica = nodeset.gen_nodes(project_id=nc.NODE_SET_PROJECT)[0]
    storage = TargetFieldsFileStorage(str(tmp_path / "replica-fields.json"))
    backend = DatabaseBackendClient(
        [
            ModelSpec(
                compute.Node,
                "set_agent_node",
                filters={},
            )
        ],
        storage,
    )

    class Driver(DirectAgentDriver):
        def get_capabilities(self):
            return ["set_agent_node"]

    driver = Driver(backend, storage)
    target = replica.to_ua_resource()
    assert target.value["default_network"] == template
    assert driver.create(target).hash == target.hash
    saved = compute.Node.objects.get_one(filters={"uuid": filters.EQ(replica.uuid)})
    compute.Node.project_observations([saved])
    original = saved.default_network
    ports = node_ports(client, saved)
    assert saved.properties.get_value("default_network") == template
    assert "default_network" not in saved.to_ua_resource().value

    nodeset.default_network = {"network": str(uuid.uuid4()), "auto_floating_ip": False}
    nodeset.update()
    target = nodeset.gen_nodes(
        project_id=nc.NODE_SET_PROJECT, node_uuids=[replica.uuid]
    )[0].to_ua_resource()
    assert driver.update(target).hash == target.hash
    saved = compute.Node.objects.get_one(filters={"uuid": filters.EQ(replica.uuid)})
    assert saved.properties.get_value("default_network") == nodeset.default_network
    compute.Node.project_observations([saved])
    assert saved.default_network["ipv4"] == original["ipv4"]
    assert saved.default_network["floating_ip"] == original["floating_ip"]
    assert node_ports(client, saved) == ports


@pytest.mark.parametrize(
    "field,value",
    [
        ("ipv4", "10.128.0.99"),
        ("floating_ip", "192.168.1.99"),
        ("port", "2cb9ffcb-5030-4c70-b6e5-5a04f8a5a49e"),
    ],
)
def test_observed_network_fields_cannot_be_written_as_a_template(client, field, value):
    node = guest(client, uuid.uuid4())
    with pytest.raises(exceptions.BadRequestError):
        client.put(
            resource(client, "compute", "nodes", node["uuid"]),
            json={"default_network": {field: value}},
        )
    assert (
        client.get(resource(client, "compute", "nodes", node["uuid"])).json()[
            "default_network"
        ]
        == node["default_network"]
    )
