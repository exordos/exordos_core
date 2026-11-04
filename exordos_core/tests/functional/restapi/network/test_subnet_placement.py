# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0

import uuid

import pytest
from bazooka import exceptions
from restalchemy.dm import filters

from exordos_core.compute.dm import models
from exordos_core.tests.functional.restapi.network.test_sdn_contract_api import (
    collection,
    create,
    resource,
)


@pytest.fixture
def client(user_api_client, auth_user_admin, monkeypatch, user_api):
    monkeypatch.setattr(
        models.MachinePool,
        "_authenticate",
        lambda self: {"project_id": "provider-project"},
        raising=False,
    )
    yield user_api_client(auth_user_admin)
    user_api._restore_seed_snapshot()


def cloud(client, **kwargs):
    spec = kwargs.pop(
        "driver_spec",
        {
            "kind": "openstack",
            "auth_url": "https://identity.example/v3",
            "auth_type": "v3password",
            "auth": {
                "username": "example",
                "password": "private-sentinel",
                "user_domain_name": "Default",
                "project_id": "provider-project",
            },
        },
    )
    return create(client, ("compute", "hypervisors"), driver_spec=spec, **kwargs)


def network(client, **kwargs):
    return create(client, ("network", "networks"), cidr="10.80.0.0/16", **kwargs)


def subnet(client, net, *hypervisors, **kwargs):
    try:
        return create(
            client,
            ("network", "subnets"),
            network=net["uuid"],
            hypervisors=[item["uuid"] for item in hypervisors],
            **kwargs,
        )
    except exceptions.BadRequestError as error:
        pytest.fail(error.cause.response.text)


def test_hypervisor_is_the_cloud_and_never_returns_credentials(client):
    hypervisor = cloud(client)
    assert hypervisor["driver_spec"]["kind"] == "openstack"
    assert "auth" not in hypervisor["driver_spec"]
    assert hypervisor["driver_state"] == {"project_id": "provider-project"}
    stored = models.MachinePool.objects.get_one(
        filters={"uuid": filters.EQ(hypervisor["uuid"])}
    )
    assert stored.driver_spec.auth["password"] == "private-sentinel"
    from exordos_core.compute.pool.dm.models import Pool

    projection = Pool.objects.get_one(filters={"uuid": filters.EQ(stored.uuid)})
    value, target = projection.get_ua_all_and_target_values()
    assert "private-sentinel" not in str((value, target))
    assert target["driver_spec"]["connection"] == hypervisor["uuid"]
    assert target["driver_spec"]["kind"] == "cloud"


def test_subnets_own_placement_and_allocate_stable_23_ranges(client):
    hypervisor = cloud(client)
    net = network(client)
    first = subnet(client, net, hypervisor)
    second = subnet(client, net, hypervisor)
    assert [first["cidr"], second["cidr"]] == ["10.80.0.0/23", "10.80.2.0/23"]
    assert first["network"] == net["uuid"]
    assert first["hypervisors"] == [hypervisor["uuid"]]
    assert {
        item["uuid"]
        for item in client.get(
            collection(client, "network", "subnets"), params={"network": net["uuid"]}
        ).json()
    } == {first["uuid"], second["uuid"]}
    changed = client.put(
        resource(client, "network", "networks", net["uuid"]), json={"name": "renamed"}
    ).json()
    assert "sites" not in changed
    assert (
        client.get(resource(client, "network", "subnets", first["uuid"])).json()["cidr"]
        == first["cidr"]
    )


def local(client, **kwargs):
    return create(
        client,
        ("compute", "hypervisors"),
        driver_spec={
            "kind": "libvirt",
            "connection_uri": "qemu+tcp://" + uuid.uuid4().hex + "/system",
            "ovs": True,
        },
        **kwargs,
    )


def mesh(client, count=2, native=False):
    defaults = {"external_network": str(uuid.uuid4()), "gateway_image": "gateway-image"}
    first = cloud(client, network_defaults=defaults)
    pools = [
        first if native or index == 0 else cloud(client, network_defaults=defaults)
        for index in range(count)
    ]
    net = network(client)
    members = [subnet(client, net, pool) for pool in pools]
    return net, pools, members


def root(net):
    return models.Network.objects.get_one(filters={"uuid": filters.EQ(net["uuid"])})


def activate(members):
    from exordos_core.network import routed

    for member in members:
        part = routed.owner(subnet=member["uuid"])
        part.status = "ACTIVE"
        part.update()


def node(client, net, **kwargs):
    return create(
        client,
        ("compute", "nodes"),
        cores=1,
        ram=512,
        disk_spec={"kind": "root_disk", "image": "cirros"},
        default_network={"network": net["uuid"]},
        **kwargs,
    )


def test_local_subnet_spans_multiple_hypervisors_without_pinning_to_one_host(client):
    from exordos_core.network import routed

    net = network(client)
    pools = [local(client, site="dc1"), local(client, site="dc1")]
    member = subnet(client, net, *pools)
    activate([member])
    pending = node(client, net)
    model = models.Node.objects.get_one(filters={"uuid": filters.EQ(pending["uuid"])})
    for pool in pools:
        stored = models.MachinePool.objects.get_one(
            filters={"uuid": filters.EQ(pool["uuid"])}
        )
        assert routed.candidate(model, stored).subnet == uuid.UUID(member["uuid"])
    unrelated = local(client, site="dc1")
    assert (
        routed.candidate(
            model,
            models.MachinePool.objects.get_one(
                filters={"uuid": filters.EQ(unrelated["uuid"])}
            ),
        )
        is False
    )


def test_cloud_subnet_cannot_span_hypervisors_or_provider_scopes(client):
    net = network(client)
    pools = [cloud(client), cloud(client)]
    with pytest.raises(exceptions.BadRequestError):
        create(
            client,
            ("network", "subnets"),
            network=net["uuid"],
            hypervisors=[pool["uuid"] for pool in pools],
        )
    foreign = cloud(client, project_id=str(uuid.uuid4()))
    with pytest.raises(exceptions.BadRequestError):
        create(
            client,
            ("network", "subnets"),
            network=net["uuid"],
            hypervisors=[foreign["uuid"]],
        )


def test_scheduler_creates_port_only_after_selecting_an_eligible_hypervisor(client):
    from exordos_core.network import routed
    from exordos_core.network.contracts import atomic_session
    from exordos_core.tests.functional.restapi.network.test_sdn_contract_api import (
        node_ports,
    )

    net, pools, members = mesh(client)
    activate(members)
    pending = node(client, net)
    assert node_ports(client, pending) == []
    model = models.Node.objects.get_one(filters={"uuid": filters.EQ(pending["uuid"])})
    chosen = models.MachinePool.objects.get_one(
        filters={"uuid": filters.EQ(pools[1]["uuid"])}
    )
    with atomic_session(models.Node._get_engine()) as session:
        routed.bind(model, chosen, session)
    assert node_ports(client, pending)[0]["subnet"] == members[1]["uuid"]
    assert (
        routed.candidate(
            model,
            models.MachinePool.objects.get_one(
                filters={"uuid": filters.EQ(pools[0]["uuid"])}
            ),
        )
        is False
    )


@pytest.mark.parametrize(
    "field,value", [("target_ipv4", "10.80.0.10"), ("auto_floating_ip", True)]
)
def test_logical_placement_requires_automatic_private_addressing(client, field, value):
    net = network(client)
    with pytest.raises(exceptions.BadRequestError):
        create(
            client,
            ("compute", "nodes"),
            cores=1,
            ram=512,
            disk_spec={"kind": "root_disk", "image": "cirros"},
            default_network={"network": net["uuid"], field: value},
        )


def test_native_subnets_share_router_without_a_public_gateway_image(
    client, monkeypatch
):
    from types import SimpleNamespace
    from exordos_core.network import routed
    from exordos_core.cloud.links import CloudNetworkLink

    pool = cloud(client)
    net = network(client)
    members = [subnet(client, net, pool), subnet(client, net, pool)]
    locals_ = [
        models.Network.objects.get_one(
            filters={
                "uuid": filters.EQ(routed.owner(subnet=member["uuid"]).local_network)
            }
        )
        for member in members
    ]
    assert routed.router_id(locals_[0]) == routed.router_id(locals_[1])
    monkeypatch.setattr(
        models.Network,
        "load_driver",
        lambda self: SimpleNamespace(list_subnets=lambda: []),
    )
    routed.reconcile(root(net))
    assert CloudNetworkLink.objects.get_all() == []


def test_openvpn_link_is_automatically_owned_and_not_publicly_mutable(
    client, monkeypatch
):
    from types import SimpleNamespace
    from exordos_core.network import routed
    from exordos_core.cloud.links import CloudNetworkLink

    net, _, members = mesh(client)
    monkeypatch.setattr(
        models.Network,
        "load_driver",
        lambda self: SimpleNamespace(list_subnets=lambda: []),
    )
    routed.reconcile(root(net))
    link = CloudNetworkLink.objects.get_one(
        filters={"owner_network": filters.EQ(net["uuid"])}
    )
    assert {endpoint["subnet"] for endpoint in link.endpoints} == {
        member["uuid"] for member in members
    }
    with pytest.raises(exceptions.BadRequestError):
        client.delete(
            resource(client, "compute", "cloud_network_links", str(link.uuid))
        )


def test_mesh_rejects_two_outbound_only_hypervisors_before_allocating_second_subnet(
    client,
):
    net = network(client)
    defaults = {
        "external_network": str(uuid.uuid4()),
        "gateway_image": "gateway",
        "role": "client",
    }
    first, second = (
        cloud(client, network_defaults=defaults),
        cloud(client, network_defaults=defaults),
    )
    subnet(client, net, first)
    with pytest.raises(exceptions.BadRequestError):
        create(
            client,
            ("network", "subnets"),
            network=net["uuid"],
            hypervisors=[second["uuid"]],
        )
    assert (
        len(
            client.get(
                collection(client, "network", "subnets"),
                params={"network": net["uuid"]},
            ).json()
        )
        == 1
    )


@pytest.mark.parametrize(
    "occupied,fresh,logical",
    [
        (False, False, True),
        (True, False, True),
        (True, True, True),
        (True, False, False),
    ],
)
def test_idle_machine_placement_obeys_binding_and_site_anti_affinity(
    client, monkeypatch, occupied, fresh, logical
):
    from exordos_core.compute.scheduler.driver import base
    from exordos_core.compute.scheduler.service import SchedulerService
    from exordos_core.tests.functional.restapi.network.test_sdn_contract_api import (
        node_ports,
    )

    net, pools, members = mesh(client, count=2 if fresh else 1)
    activate(members)
    if not logical:
        pools = [local(client)]
    stored_pools = [
        models.MachinePool.objects.get_one(filters={"uuid": filters.EQ(pool["uuid"])})
        for pool in pools
    ]
    project = uuid.UUID(int=0)
    policy = models.PlacementPolicy(
        project_id=project,
        kind="soft-anti-affinity" if fresh else "anti-affinity",
        scope="site",
    )
    policy.insert()

    def make_node():
        model = models.Node.restore_from_simple_view(
            project_id=str(project),
            cores=1,
            ram=512,
            disk_spec={"kind": "root_disk", "image": "cirros"},
            default_network={"network": net["uuid"]} if logical else {},
            placement_policies=[str(policy.uuid)],
        )
        model.insert()
        return model

    pending = make_node()
    if occupied:
        other = make_node()
        models.Machine(
            project_id=project,
            node=other.uuid,
            pool=stored_pools[0].uuid,
            cores=1,
            ram=512,
        ).insert()
    idle = models.Machine(
        project_id=project, pool=stored_pools[0].uuid, cores=1, ram=512, status="IDLE"
    )
    idle.insert()
    scheduler = SchedulerService([], [], [], [])
    bundle = base.NodeBundle(node=pending, volumes=[])
    monkeypatch.setattr(scheduler, "_hypervisor_nodes", lambda: set())
    monkeypatch.setattr(scheduler, "_get_unscheduled_nodes", lambda: [bundle])
    monkeypatch.setattr(
        scheduler,
        "_get_idle_machines",
        lambda: [base.MachineBundle(machine=idle, volumes=[])],
    )
    remaining = scheduler._schedule_on_existing_machines(
        [base.MachinePoolBundle(pool=pool, volumes=[]) for pool in stored_pools]
    )
    stored = models.Machine.objects.get_one(filters={"uuid": filters.EQ(idle.uuid)})
    if occupied:
        assert stored.node is None
        assert remaining == [bundle]
        assert node_ports(client, pending) == []
    else:
        assert stored.node == pending.uuid
        assert node_ports(client, pending)[0]["subnet"] == members[0]["uuid"]


def test_mutations_preserve_network_and_subnet_placement(client):
    net, pools, members = mesh(client, native=True)
    with pytest.raises(exceptions.ConflictError):
        client.put(
            resource(client, "network", "networks", net["uuid"]),
            json={"cidr": "10.90.0.0/16"},
        )
    other = cloud(client)
    with pytest.raises(exceptions.ConflictError):
        client.put(
            resource(client, "network", "subnets", members[0]["uuid"]),
            json={"hypervisors": [other["uuid"]]},
        )
    with pytest.raises(exceptions.ConflictError):
        client.delete(resource(client, "compute", "hypervisors", pools[0]["uuid"]))
    with pytest.raises(exceptions.ForbiddenError):
        client.put(
            resource(client, "network", "subnets", members[0]["uuid"]),
            json={"status": "ACTIVE"},
        )


def test_import_preserves_cidr_and_existing_workloads_and_detaches_without_deleting(
    client,
):
    from exordos_core.compute.node_set.dm.models import NodeSet
    from exordos_core.network import routed

    pool = local(client)
    backend = create(
        client, ("network", "networks"), driver_spec={"driver": "ovs_evpn"}
    )
    original = create(
        client, ("network", "subnets"), network=backend["uuid"], cidr="10.99.0.0/24"
    )
    group = NodeSet.restore_from_simple_view(
        project_id=str(uuid.UUID(int=0)),
        cores=1,
        ram=512,
        disk_spec={"kind": "root_disk", "image": "cirros"},
        default_network={"subnet": original["uuid"]},
    )
    group.insert()
    net = network(client)
    imported = client.put(
        resource(client, "network", "subnets", original["uuid"]),
        json={"network": net["uuid"], "hypervisors": [pool["uuid"]]},
    ).json()
    assert imported["cidr"] == original["cidr"]
    assert imported["network"] == net["uuid"]
    with pytest.raises(exceptions.ConflictError):
        client.delete(resource(client, "network", "networks", net["uuid"]))
    replica = group.gen_nodes(group.project_id)[0]
    replica.insert()
    replica.delete()
    group.delete()
    client.delete(resource(client, "network", "networks", net["uuid"]))
    routed.reconcile(root(net))
    preserved = client.get(
        resource(client, "network", "subnets", original["uuid"])
    ).json()
    assert preserved["network"] == backend["uuid"]
    assert preserved["cidr"] == original["cidr"]


def test_import_cannot_overlap_allocated_ranges(client):
    pool = local(client)
    net = network(client)
    subnet(client, net, pool)
    backend = create(
        client, ("network", "networks"), driver_spec={"driver": "ovs_evpn"}
    )
    original = create(
        client, ("network", "subnets"), network=backend["uuid"], cidr="10.80.0.0/24"
    )
    with pytest.raises(exceptions.BadRequestError):
        client.put(
            resource(client, "network", "subnets", original["uuid"]),
            json={"network": net["uuid"], "hypervisors": [pool["uuid"]]},
        )


def test_subnet_cleanup_retains_allocation_on_failure_and_retries(client, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from exordos_core.network import routed

    net, _, members = mesh(client, count=1)
    driver = SimpleNamespace(
        delete_subnet=Mock(side_effect=[RuntimeError("provider unavailable"), None]),
        delete_network=Mock(),
    )
    monkeypatch.setattr(models.Network, "load_driver", lambda self: driver)
    client.delete(resource(client, "network", "subnets", members[0]["uuid"]))
    routed.reconcile(root(net))
    observed = client.get(
        resource(client, "network", "subnets", members[0]["uuid"])
    ).json()
    assert observed["status"] == "DELETING"
    assert observed["cidr"] == members[0]["cidr"]
    routed.reconcile(root(net))
    with pytest.raises(exceptions.NotFoundError):
        client.get(resource(client, "network", "subnets", members[0]["uuid"]))
    assert driver.delete_subnet.call_count == 2
    driver.delete_network.assert_called_once()


def test_group_membership_rules_resolve_only_addresses_in_the_same_logical_network(
    client,
):
    import netaddr
    from exordos_core.network import routed
    from exordos_core.user_api.network.dm import models as catalog

    net, _, members = mesh(client)
    group = create(client, ("network", "catalog", "security_groups"))
    ports = []
    for index, member in enumerate(members):
        row = create(client, ("network", "ports"), subnet=member["uuid"])
        port = models.Port.objects.get_one(filters={"uuid": filters.EQ(row["uuid"])})
        port.ipv4 = netaddr.IPAddress(f"10.80.{index * 2}.10")
        port.update()
        catalog.PortSecurityGroup(
            project_id=uuid.UUID(int=0),
            port=port.uuid,
            security_group=uuid.UUID(group["uuid"]),
        ).insert()
        ports.append(port)
    rule = {
        "direction": "ingress",
        "protocol": "tcp",
        "port": 5432,
        "remote_group_id": group["uuid"],
    }
    assert {
        rule["remote_ip"] for rule in routed.expand_security_rules(ports[0], [rule])
    } == {"10.80.0.10/32", "10.80.2.10/32"}
    catalog.PortSecurityGroup.objects.get_one(
        filters={"port": filters.EQ(ports[1].uuid)}
    ).delete()
    assert [
        rule["remote_ip"] for rule in routed.expand_security_rules(ports[0], [rule])
    ] == ["10.80.0.10/32"]


def test_paas_uses_authorized_network_and_workload_project_for_its_ports(client):
    from exordos_core.compute import constants
    from exordos_core.compute.builders.node_set import NodeSetBuilderService
    from exordos_core.compute.node_set.dm.models import NodeSet
    from exordos_core.network import routed
    from exordos_core.network.contracts import atomic_session
    from exordos_core.tests.functional.restapi.network.test_sdn_contract_api import (
        node_ports,
    )

    net, pools, members = mesh(client, count=1)
    activate(members)
    project = uuid.uuid4()
    group = NodeSet.restore_from_simple_view(
        project_id=str(project),
        cores=1,
        ram=512,
        disk_spec={"kind": "disks", "disks": [{"image": "cirros", "size": 1}]},
        default_network={"network": net["uuid"], "project_id": str(uuid.UUID(int=0))},
    )
    group._allow_foreign_subnet = True
    group.insert()
    loaded = NodeSet.objects.get_one(filters={"uuid": filters.EQ(group.uuid)})
    builder = NodeSetBuilderService(NodeSet, constants.NODE_SET_PROJECT)
    replica = next(
        item for item in builder.create_infra(loaded) if isinstance(item, models.Node)
    )
    assert all(not volume.ephemeral for volume in replica.disk_spec.volumes(replica))
    replica.insert()
    pool = models.MachinePool.objects.get_one(
        filters={"uuid": filters.EQ(pools[0]["uuid"])}
    )
    with atomic_session(models.Node._get_engine()) as session:
        routed.bind(replica, pool, session)
    assert node_ports(client, replica)[0]["project_id"] == str(project)


def test_read_only_user_cannot_create_network_infrastructure(
    user_api_client, auth_test1_user, client
):
    net = network(client)
    pool = cloud(client)
    user = user_api_client(auth_test1_user, permissions=["network.subnet.create"])
    with pytest.raises(exceptions.ForbiddenError):
        user.post(
            collection(user, "network", "subnets"),
            json={
                "project_id": str(uuid.UUID(int=0)),
                "network": net["uuid"],
                "hypervisors": [pool["uuid"]],
            },
        )


@pytest.mark.parametrize("selector", ["subnet", "network"])
def test_new_workloads_reference_the_logical_network_not_backing_resources(
    client, selector
):
    from exordos_core.network import routed

    net, _, members = mesh(client, count=1)
    part = routed.owner(subnet=members[0]["uuid"])
    ref = str(part.subnet if selector == "subnet" else part.local_network)
    with pytest.raises(exceptions.BadRequestError):
        create(
            client,
            ("compute", "nodes"),
            cores=1,
            ram=512,
            disk_spec={"kind": "root_disk", "image": "cirros"},
            default_network={selector: ref},
        )


def test_hardware_and_existing_nodes_cannot_change_logical_placement(client):
    net = network(client)
    with pytest.raises(exceptions.BadRequestError):
        node(client, net, node_type="HW")
    pending = node(client, net)
    with pytest.raises(exceptions.ConflictError):
        client.put(
            resource(client, "compute", "nodes", pending["uuid"]),
            json={"default_network": {}},
        )
    with pytest.raises(exceptions.ConflictError):
        client.delete(resource(client, "network", "networks", net["uuid"]))


def test_populated_nodeset_cannot_change_network(client):
    from exordos_core.compute.node_set.dm.models import NodeSet
    from restalchemy.storage import exceptions as storage_exceptions

    net = network(client)
    group = NodeSet.restore_from_simple_view(
        project_id=str(uuid.UUID(int=0)),
        cores=1,
        ram=512,
        disk_spec={"kind": "root_disk", "image": "cirros"},
        default_network={"network": net["uuid"]},
    )
    group.insert()
    group.gen_nodes(group.project_id)[0].insert()
    group.default_network = {}
    with pytest.raises(storage_exceptions.ConflictRecords):
        group.update()


def test_user_address_reservation_blocks_subnet_and_network_removal(client):
    net, _, members = mesh(client, count=1)
    create(
        client,
        ("network", "catalog", "addresses"),
        subnet=members[0]["uuid"],
        kind="reservation",
    )
    for path in (("subnets", members[0]["uuid"]), ("networks", net["uuid"])):
        with pytest.raises(exceptions.ConflictError):
            client.delete(resource(client, "network", *path))


def test_managed_gateway_reservation_does_not_block_network_delete_intent(
    client, monkeypatch
):
    from types import SimpleNamespace
    from exordos_core.cloud.links import CloudLinkEndpoint, CloudNetworkLink
    from exordos_core.network import routed
    from exordos_core.user_api.network.dm import models as catalog

    net, _, members = mesh(client)
    monkeypatch.setattr(
        models.Network,
        "load_driver",
        lambda self: SimpleNamespace(list_subnets=lambda: []),
    )
    routed.reconcile(root(net))
    link = CloudNetworkLink.objects.get_one(
        filters={"owner_network": filters.EQ(net["uuid"])}
    )
    endpoint = CloudLinkEndpoint.objects.get_one(
        filters={
            "link": filters.EQ(link.uuid),
            "subnet": filters.EQ(members[0]["uuid"]),
        }
    )
    catalog.Address(
        uuid=endpoint.uuid,
        project_id=uuid.UUID(int=0),
        subnet=uuid.UUID(members[0]["uuid"]),
        kind="reservation",
        address="10.80.0.10",
    ).insert()
    client.delete(resource(client, "network", "networks", net["uuid"]))
    assert all(part.status == "DELETING" for part in routed.parts(root(net)))


def test_foreign_link_blocks_subnet_cleanup_before_provider_resources_exist(client):
    from exordos_core.tests.functional.restapi.compute.test_cloud_api import (
        link_endpoints,
    )

    net, _, members = mesh(client, count=1)
    create(
        client,
        ("compute", "cloud_network_links"),
        endpoints=[
            {
                "subnet": members[0]["uuid"],
                "external_network": str(uuid.uuid4()),
                "gateway_image": "ubuntu",
            },
            link_endpoints(client, count=1)[0],
        ],
    )
    with pytest.raises(exceptions.ConflictError):
        client.delete(resource(client, "network", "subnets", members[0]["uuid"]))


def test_cidr_is_reusable_only_after_subnet_cleanup_completes(client, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from exordos_core.network import routed

    net, pools, members = mesh(client, count=1)
    first = members[0]
    client.delete(resource(client, "network", "subnets", first["uuid"]))
    second = subnet(client, net, pools[0])
    assert second["cidr"] != first["cidr"]
    monkeypatch.setattr(
        models.Network,
        "load_driver",
        lambda self: SimpleNamespace(
            delete_subnet=Mock(), delete_network=Mock(), list_subnets=lambda: []
        ),
    )
    routed.reconcile(root(net))
    third = subnet(client, net, pools[0])
    assert third["cidr"] == first["cidr"]


@pytest.mark.parametrize("cloud_backend", [False, True])
def test_imported_subnet_controls_paas_disk_defaults(client, cloud_backend):
    from exordos_core.compute.node_set.dm.models import NodeSet

    pool = cloud(client) if cloud_backend else local(client)
    driver = (
        {"driver": "cloud", "connection": pool["uuid"]}
        if cloud_backend
        else {"driver": "ovs_evpn"}
    )
    backend = create(client, ("network", "networks"), driver_spec=driver)
    original = create(
        client, ("network", "subnets"), network=backend["uuid"], cidr="10.99.0.0/24"
    )
    net = network(client)
    client.put(
        resource(client, "network", "subnets", original["uuid"]),
        json={"network": net["uuid"], "hypervisors": [pool["uuid"]]},
    )
    group = NodeSet.restore_from_simple_view(
        project_id=str(uuid.UUID(int=0)),
        cores=1,
        ram=512,
        disk_spec={"kind": "disks", "disks": [{"image": "cirros", "size": 1}]},
        default_network={"network": net["uuid"]},
    )
    replica = group.gen_nodes(group.project_id)[0]
    assert all(
        volume.ephemeral is not cloud_backend
        for volume in replica.disk_spec.volumes(replica)
    )


def test_hypervisor_credentials_can_switch_authentication_method_in_the_same_scope(
    client,
):
    hypervisor = cloud(client)
    spec = {
        **hypervisor["driver_spec"],
        "auth_type": "v3applicationcredential",
        "auth": {
            "application_credential_id": "replacement",
            "application_credential_secret": "new-private-sentinel",
        },
    }
    changed = client.put(
        resource(client, "compute", "hypervisors", hypervisor["uuid"]),
        json={"driver_spec": spec},
    ).json()
    assert changed["driver_spec"]["auth_type"] == "v3applicationcredential"
    assert "new-private-sentinel" not in str(changed)


def test_idle_reconciliation_does_not_rewrite_observed_state(client, monkeypatch):
    from types import SimpleNamespace
    from exordos_core.network import routed

    pool = local(client)
    net = network(client)
    member = subnet(client, net, pool)
    monkeypatch.setattr(
        models.Network,
        "load_driver",
        lambda self: SimpleNamespace(
            list_subnets=lambda: [SimpleNamespace(uuid=uuid.UUID(member["uuid"]))]
        ),
    )
    routed.reconcile(root(net))
    part = routed.owner(subnet=member["uuid"])
    observed = models.Subnet.objects.get_one(
        filters={"uuid": filters.EQ(member["uuid"])}
    )
    stamp = (root(net).updated_at, part.updated_at, observed.updated_at)
    routed.reconcile(root(net))
    assert stamp == (
        root(net).updated_at,
        routed.owner(subnet=member["uuid"]).updated_at,
        models.Subnet.objects.get_one(
            filters={"uuid": filters.EQ(member["uuid"])}
        ).updated_at,
    )


def test_public_logical_network_allows_allocation_in_imported_private_subnet(client):
    from exordos_core.user_api.network.dm import models as catalog

    pool = local(client)
    backend = create(
        client, ("network", "networks"), driver_spec={"driver": "ovs_evpn"}
    )
    original = create(
        client, ("network", "subnets"), network=backend["uuid"], cidr="10.99.0.0/24"
    )
    net = network(client, access="public")
    client.put(
        resource(client, "network", "subnets", original["uuid"]),
        json={"network": net["uuid"], "hypervisors": [pool["uuid"]]},
    )
    stored = models.Subnet.objects.get_one(
        filters={"uuid": filters.EQ(original["uuid"])}
    )
    assert catalog.may_allocate_in(uuid.uuid4(), stored)
    assert (
        models.Network.objects.get_one(
            filters={"uuid": filters.EQ(backend["uuid"])}
        ).access
        == "private"
    )


def test_hardware_nodeset_cannot_request_logical_network(client):
    net = network(client)
    with pytest.raises(exceptions.BadRequestError):
        create(
            client,
            ("compute", "sets"),
            node_type="HW",
            cores=1,
            ram=512,
            disk_spec={"kind": "root_disk", "image": "cirros"},
            default_network={"network": net["uuid"]},
        )


def test_import_cannot_change_the_existing_address_range(client):
    pool = local(client)
    backend = create(
        client, ("network", "networks"), driver_spec={"driver": "ovs_evpn"}
    )
    original = create(
        client, ("network", "subnets"), network=backend["uuid"], cidr="10.99.0.0/24"
    )
    net = network(client)
    with pytest.raises(exceptions.ConflictError):
        client.put(
            resource(client, "network", "subnets", original["uuid"]),
            json={
                "network": net["uuid"],
                "hypervisors": [pool["uuid"]],
                "ip_range": "10.99.0.10-10.99.0.100",
            },
        )


def test_owned_local_gateway_bootstraps_in_a_logical_subnet(client, monkeypatch):
    from types import SimpleNamespace
    from exordos_core.cloud import local as local_gateway
    from exordos_core.cloud.links import CloudNetworkLink, endpoints_for_subnet
    from exordos_core.compute.scheduler.service import SchedulerService
    from exordos_core.network import routed

    uplink_network = models.Network(
        project_id=uuid.UUID(int=0), driver_spec={"driver": "flat_bridge"}
    )
    uplink_network.insert()
    uplink = create(
        client,
        ("network", "subnets"),
        network=str(uplink_network.uuid),
        cidr="192.0.2.0/24",
    )
    allowed = local(
        client,
        network_defaults={
            "role": "client",
            "external_subnet": uplink["uuid"],
            "gateway_image": "https://images.example/gateway.raw.zst",
        },
    )
    forbidden = local(client)
    remote = cloud(
        client,
        network_defaults={
            "role": "server",
            "external_network": str(uuid.uuid4()),
            "gateway_image": "ubuntu",
        },
    )
    net = network(client)
    member = subnet(client, net, allowed)
    subnet(client, net, remote)
    monkeypatch.setattr(
        models.Network,
        "load_driver",
        lambda self: SimpleNamespace(list_subnets=lambda: []),
    )
    routed.reconcile(root(net))
    link = CloudNetworkLink.objects.get_one(
        filters={"owner_network": filters.EQ(net["uuid"])}
    )
    endpoint = endpoints_for_subnet(uuid.UUID(member["uuid"]))[0]
    initial = local_gateway.reserve(link, endpoint)
    assert initial["endpoint"] and initial["gateway_ip"]
    assert local_gateway.reserve(link, endpoint) == initial
    gateway = models.Node.objects.get_one(filters={"uuid": filters.EQ(endpoint.uuid)})
    assert (
        len(models.Port.objects.get_all(filters={"node": filters.EQ(gateway.uuid)}))
        == 2
    )
    for pool, expected in ((allowed, True), (forbidden, False)):
        stored = models.MachinePool.objects.get_one(
            filters={"uuid": filters.EQ(pool["uuid"])}
        )
        assert SchedulerService._network_matches_pool(gateway, stored) is expected

    client.delete(resource(client, "network", "networks", net["uuid"]))
    assert all(part.status == "DELETING" for part in routed.parts(root(net)))
