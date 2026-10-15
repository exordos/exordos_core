# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0

from concurrent.futures import ThreadPoolExecutor
import uuid

from bazooka import exceptions
import pytest
from restalchemy.dm import filters

from exordos_core.common import config
from exordos_core.compute import constants as nc
from exordos_core.compute.dm import models as compute
from exordos_core.compute.node_set.dm import models as node_set_models
from exordos_core.tests.functional.restapi.network.test_sdn_contract_api import (
    collection,
)
from exordos_core.tests.functional.restapi.network.test_sdn_contract_api import create
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


def test_node_and_port_share_lazy_default_without_iam_project(client):
    project_id = uuid.uuid4()
    assert (
        iam.Project.objects.get_one_or_none(filters={"uuid": filters.EQ(project_id)})
        is None
    )
    node = guest(client, project_id)
    assert len(node["ports"]) == 1
    port = node["ports"][0]
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
    assert node["ports"] == [] and node["default_network"] == {}
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
        assert len(node.ports) == 1
    else:
        assert len(body["ports"]) == 1
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
    assert node["ports"][0]["network"] == private["uuid"]
    with pytest.raises(exceptions.ForbiddenError):
        create(creator, ("network", "ports"), network=private["uuid"])


def test_automatic_fip_still_requires_address_creation(
    client, user_api_client, auth_test1_user
):
    pool(client, default_floating_pool=True)
    creator = user_api_client(auth_test1_user, permissions=["compute.node.create"])
    with pytest.raises(exceptions.ForbiddenError):
        guest(creator, uuid.UUID(int=0), default_network={"floating_ip": True})
    with_address = user_api_client(
        auth_test1_user, permissions=["network.address.create"]
    )
    node = guest(with_address, uuid.UUID(int=0), default_network={"floating_ip": True})
    assert len(node["ports"]) == 1


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
    port = compute.Port(
        project_id=project_id, subnet=subnet.uuid, legacy_flat_placement=True
    )
    port.insert()
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
    assert node["ports"][0]["subnet"] == port["subnet"] == str(previous.subnet)
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
        created["ipv4"] if standalone else created["ports"][0]["ipv4"]
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
        default_network={"target_ipv4": "10.128.0.50", "floating_ip": True},
    )
    assert node["ports"][0]["ipv4"] == "10.128.0.50"


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
        assert node["ports"][0]["subnet"] != str(previous.subnet)
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
    assert node.ports[0]["subnet"] == str(previous.subnet)


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
            "floating_ip": True if automatic_pool else {"subnet": public["uuid"]}
        },
    )
    port = node["ports"][0]
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
        assert node["ports"] == []
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
    assert len(node.ports) == 1
    assert node.ports[0]["index"] == 0
    assert node.ports[0]["ipv4"] == "10.20.0.2"
    assert node.ports[0]["subnet"] == str(subnet.uuid)


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
        {"ipv4": "10.128.0.2"},
        {"network": None},
        {"network": str(uuid.uuid4()), "subnet": str(uuid.uuid4())},
    ],
)
def test_invalid_template_is_rejected_before_creation(client, value):
    project_id = uuid.uuid4()
    with pytest.raises(exceptions.BadRequestError):
        guest(client, project_id, default_network=value)
    assert (
        compute.Node.objects.get_all(filters={"project_id": filters.EQ(project_id)})
        == []
    )


def test_automatic_fip_uses_marked_default_pool(client):
    pool(client)
    preferred = pool(client, "203.0.113.0/28", default_floating_pool=True)
    project_id = uuid.uuid4()
    node = guest(client, project_id, default_network={"floating_ip": True})
    addresses = client.get(
        collection(client, "network", "catalog", "addresses"),
        params={"port": node["ports"][0]["uuid"]},
    ).json()
    assert len(addresses) == 1
    assert addresses[0]["subnet"] == preferred["uuid"]
    assert addresses[0]["project_id"] == str(project_id)


def test_automatic_fip_uses_only_available_pool(client):
    available = pool(client)
    node = guest(client, uuid.uuid4(), default_network={"floating_ip": True})
    address = catalog.Address.objects.get_one(
        filters={"port": filters.EQ(node["ports"][0]["uuid"])}
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
    node = guest(client, uuid.uuid4(), default_network={"floating_ip": True})
    address = catalog.Address.objects.get_one(
        filters={"port": filters.EQ(node["ports"][0]["uuid"])}
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
        guest(client, project_id, default_network={"floating_ip": True})
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


def test_node_creation_template_cannot_be_updated(client):
    node = guest(client, uuid.uuid4())
    with pytest.raises(exceptions.ForbiddenError):
        client.put(
            resource(client, "compute", "nodes", node["uuid"]),
            json={"default_network": {}},
        )
    assert (
        client.get(resource(client, "compute", "nodes", node["uuid"])).json()["ports"]
        == node["ports"]
    )


def test_removing_last_port_does_not_create_another(client):
    from exordos_core.network.service import NetworkService

    node = guest(client, uuid.uuid4())
    client.delete(resource(client, "network", "ports", node["ports"][0]["uuid"]))
    stored = compute.Node.objects.get_one(filters={"uuid": filters.EQ(node["uuid"])})
    NetworkService()._reconcile_interfaces(stored, [], None, {})
    assert stored.ports == [] and stored.default_network == {}


def test_upgrade_preserves_existing_ports_and_old_empty_set_templates(client):
    from pathlib import Path
    import runpy

    from restalchemy.storage.sql import engines

    node = guest(client, uuid.uuid4())
    body = create(
        client,
        ("compute", "sets"),
        replicas=1,
        cores=1,
        ram=1024,
        disk_spec={"kind": "root_disk", "image": "ubuntu_24.04"},
        default_network={},
    )
    step = runpy.run_path(
        str(Path(__file__).parents[5] / "migrations/0009-default-network-a182bc39.py")
    )["migration_step"]
    with engines.engine_factory.get_engine().session_manager() as session:
        step.downgrade(session)
        step.upgrade(session)
    stored = compute.Node.objects.get_one(filters={"uuid": filters.EQ(node["uuid"])})
    assert stored.ports == node["ports"]
    assert stored.default_network == node["default_network"]
    nodeset = node_set_models.NodeSet.objects.get_one(
        filters={"uuid": filters.EQ(body["uuid"])}
    )
    assert nodeset.default_network is None


@pytest.mark.parametrize("entries, expected", [([], {}), ([{"port": {}}], None)])
def test_upgrade_removes_saved_networking_without_losing_intent(
    client, entries, expected
):
    import json
    from pathlib import Path
    import runpy

    from gcl_sdk.agents.universal import utils as sdk_utils
    from gcl_sdk.agents.universal.dm import models as ua
    from restalchemy.storage.sql import engines

    body = create(
        client,
        ("compute", "sets"),
        replicas=1,
        cores=1,
        ram=1024,
        disk_spec={"kind": "root_disk", "image": "ubuntu_24.04"},
    )
    request = ua.TargetResource(
        value={
            "uuid": body["uuid"],
            "networking": entries,
            "network_activation_barrier": {"rules": []},
        },
        kind="em_core_compute_sets",
    )
    request.insert()
    step = runpy.run_path(
        str(Path(__file__).parents[5] / "migrations/0009-default-network-a182bc39.py")
    )["migration_step"]
    with engines.engine_factory.get_engine().session_manager() as session:
        step.downgrade(session)
        session.execute(
            "UPDATE compute_sets SET networking = %s::jsonb[] WHERE uuid = %s",
            ([json.dumps(entry) for entry in entries], body["uuid"]),
        )
        step.upgrade(session)
    stored = node_set_models.NodeSet.objects.get_one(
        filters={"uuid": filters.EQ(body["uuid"])}
    )
    assert stored.default_network == expected
    current = ua.TargetResource.objects.get_one(
        filters={"uuid": filters.EQ(request.uuid)}
    )
    assert (
        "networking" not in current.value
        and "network_activation_barrier" not in current.value
    )
    assert current.value.get("default_network") == expected
    assert ("default_network" in current.value) == (expected is not None)
    assert current.hash == sdk_utils.calculate_hash(current.value)


@pytest.mark.parametrize("multiple", [False, True])
def test_missing_or_ambiguous_pool_rolls_back(client, multiple):
    if multiple:
        pool(client)
        pool(client, "203.0.113.0/28")
    project_id = uuid.uuid4()
    expected = exceptions.ConflictError if multiple else exceptions.BadRequestError
    with pytest.raises(expected):
        guest(client, project_id, default_network={"floating_ip": True})
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
    node = guest(
        client,
        uuid.UUID(int=0),
        default_network={
            selector: private["uuid"] if selector == "network" else subnet["uuid"],
            "floating_ip": {"subnet": public["uuid"]},
        },
    )
    assert node["ports"][0]["subnet"] == subnet["uuid"]
    assert node["default_network"]["ipv4"] == "10.42.0.2"


def test_nodeset_allocates_distinct_ports_and_fips_in_owner_project(client):
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
        default_network={"floating_ip": True},
    )
    nodeset = node_set_models.NodeSet.objects.get_one(
        filters={"uuid": filters.EQ(body["uuid"])}
    )
    nodes = nodeset.gen_nodes(project_id=nc.NODE_SET_PROJECT)
    for node in nodes:
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
    assert len(node.ports) == (1 if template is None else 0)


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
    port_id = original.ports[0]["uuid"]
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
    assert fresh.ports == []
    assert (
        compute.Node.objects.get_one(filters={"uuid": filters.EQ(original.uuid)}).ports[
            0
        ]["uuid"]
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
                    "default_network": {"floating_ip": True},
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
        [ModelSpec(compute.Node, item.kind, filters={})], storage
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
    # The Node request is create-only even if the manifest changes its template.
    item.value = {**item.value, "default_network": {}}
    item.update()
    item.actualize()
    driver.update(item.target_resource)
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
