# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0

import uuid

from bazooka import exceptions
import pytest
from restalchemy.dm import filters

from exordos_core.compute import constants as nc
from exordos_core.compute.dm import models as compute
from exordos_core.compute.node_set.dm import models as sets
from exordos_core.tests.functional.restapi.network.test_default_network import guest
from exordos_core.tests.functional.restapi.network.test_default_network import (
    legacy_flat_port,
)
from exordos_core.tests.functional.restapi.network.test_default_network import pool
from exordos_core.tests.functional.restapi.network.test_sdn_contract_api import create
from exordos_core.tests.functional.restapi.network.test_sdn_contract_api import resource
from exordos_core.user_api.network.dm import models as catalog


@pytest.fixture
def client(user_api_client, auth_user_admin, user_api):
    yield user_api_client(auth_user_admin)
    user_api._restore_seed_snapshot()


def stored(model, uid):
    return model.objects.get_one_or_none(filters={"uuid": filters.EQ(uid)})


@pytest.mark.parametrize("published", [False, True])
def test_node_deletes_its_port_and_automatic_fip(client, published):
    pool(client, default_floating_pool=True)
    node = guest(client, uuid.uuid4(), default_network={"floating_ip": True})
    port = node["ports"][0]
    address = catalog.Address.objects.get_one(
        filters={"port": filters.EQ(port["uuid"])}
    )
    if published:
        address.claim_publication(uuid.UUID(port["uuid"]))
    client.delete(resource(client, "compute", "nodes", node["uuid"]))
    assert stored(compute.Port, port["uuid"]) is None
    address = stored(catalog.Address, address.uuid)
    if published:
        assert address.delete_requested and address.port is None
        assert address.association == uuid.UUID(port["uuid"])
        assert address.allocation == "reserved"
    else:
        assert address is None


def test_detached_port_outlives_its_former_owner(client):
    node = guest(client, uuid.uuid4())
    uid = node["ports"][0]["uuid"]
    client.put(resource(client, "network", "ports", uid), json={"node": None})
    client.delete(resource(client, "compute", "nodes", node["uuid"]))
    assert stored(compute.Port, uid).owner_node is None


def test_independent_port_outlives_node(client):
    project = uuid.uuid4()
    node = guest(client, project, default_network={})
    port = create(
        client, ("network", "ports"), project_id=str(project), node=node["uuid"]
    )
    client.delete(resource(client, "compute", "nodes", node["uuid"]))
    saved = stored(compute.Port, port["uuid"])
    assert saved is not None and saved.node is None and saved.owner_node is None


@pytest.mark.parametrize("published", [False, True])
def test_independent_fip_outlives_owned_port(client, published):
    public = pool(client)
    project = uuid.uuid4()
    node = guest(client, project)
    port = node["ports"][0]
    fip = create(
        client,
        ("network", "catalog", "addresses"),
        project_id=str(project),
        kind="floating",
        subnet=public["uuid"],
        port=port["uuid"],
    )
    address = stored(catalog.Address, fip["uuid"])
    if published:
        address.claim_publication(uuid.UUID(port["uuid"]))
    client.delete(resource(client, "compute", "nodes", node["uuid"]))
    assert stored(compute.Port, port["uuid"]) is None
    saved = stored(catalog.Address, address.uuid)
    assert saved is not None and saved.port is None
    assert saved.owner_port is None and not saved.delete_requested
    assert bool(saved.association) == published


def test_nodeset_deletes_replica_ports_and_fips(client):
    pool(client, default_floating_pool=True)
    body = create(
        client,
        ("compute", "sets"),
        project_id=str(uuid.uuid4()),
        cores=1,
        ram=1024,
        replicas=2,
        disk_spec={"kind": "root_disk", "image": "ubuntu_24.04"},
        default_network={"floating_ip": True},
    )
    node_set = stored(sets.NodeSet, body["uuid"])
    nodes = node_set.gen_nodes(project_id=nc.NODE_SET_PROJECT)
    for node in nodes:
        node.insert()
    ports = [node.ports[0]["uuid"] for node in nodes]
    fips = [
        catalog.Address.objects.get_one(filters={"port": filters.EQ(uid)}).uuid
        for uid in ports
    ]
    client.delete(resource(client, "compute", "sets", body["uuid"]))
    assert all(stored(compute.Node, node.uuid) is None for node in nodes)
    assert all(stored(compute.Port, uid) is None for uid in ports)
    assert all(stored(catalog.Address, uid) is None for uid in fips)


def test_port_detach_releases_owner_and_allows_adoption(client):
    project = uuid.uuid4()
    first, second = guest(client, project), guest(client, project, default_network={})
    uid = first["ports"][0]["uuid"]
    url = resource(client, "network", "ports", uid)
    assert client.put(url, json={"node": None}).json().get("owner_node") is None
    client.put(url, json={"node": second["uuid"], "owner_node": second["uuid"]})
    client.delete(resource(client, "compute", "nodes", first["uuid"]))
    assert stored(compute.Port, uid).owner_node == uuid.UUID(second["uuid"])
    client.delete(resource(client, "compute", "nodes", second["uuid"]))
    assert stored(compute.Port, uid) is None


def test_fip_detach_releases_owner_and_allows_adoption(client):
    pool(client, default_floating_pool=True)
    project = uuid.uuid4()
    node = guest(client, project, default_network={"floating_ip": True})
    port = node["ports"][0]
    address = catalog.Address.objects.get_one(
        filters={"port": filters.EQ(port["uuid"])}
    )
    other = create(client, ("network", "ports"), project_id=str(project))
    url = resource(client, "network", "catalog", "addresses", str(address.uuid))
    assert client.put(url, json={"port": None}).json().get("owner_port") is None
    client.put(url, json={"port": other["uuid"], "owner_port": other["uuid"]})
    client.delete(resource(client, "network", "ports", port["uuid"]))
    assert stored(catalog.Address, address.uuid).owner_port == uuid.UUID(other["uuid"])
    client.delete(resource(client, "network", "ports", other["uuid"]))
    assert stored(catalog.Address, address.uuid) is None


def test_ownership_can_be_released_without_detaching(client):
    project = uuid.uuid4()
    node = guest(client, project)
    port = node["ports"][0]
    url = resource(client, "network", "ports", port["uuid"])
    released = client.put(url, json={"owner_node": None}).json()
    assert released["node"] == node["uuid"] and released.get("owner_node") is None
    client.delete(resource(client, "compute", "nodes", node["uuid"]))
    assert stored(compute.Port, port["uuid"]) is not None


def test_fip_ownership_can_be_released_without_unbinding(client):
    pool(client, default_floating_pool=True)
    node = guest(client, uuid.uuid4(), default_network={"floating_ip": True})
    port = node["ports"][0]
    address = catalog.Address.objects.get_one(
        filters={"port": filters.EQ(port["uuid"])}
    )
    client.put(
        resource(client, "network", "catalog", "addresses", str(address.uuid)),
        json={"owner_port": None},
    )
    client.delete(resource(client, "compute", "nodes", node["uuid"]))
    assert stored(catalog.Address, address.uuid).owner_port is None
    assert stored(catalog.Address, address.uuid).port is None


def test_adopting_port_requires_node_update_permission(
    client, user_api_client, auth_test1_user
):
    node = guest(client, uuid.UUID(int=0), default_network={})
    port = create(client, ("network", "ports"), node=node["uuid"])
    limited = user_api_client(auth_test1_user, permissions=["network.port.update"])
    url = resource(limited, "network", "ports", port["uuid"])
    with pytest.raises(exceptions.ForbiddenError):
        limited.put(url, json={"owner_node": node["uuid"]})


def test_nodeset_reports_fip_and_manages_replica_ownership(client):
    from unittest import mock

    from exordos_core.compute.builders.node_set import NodeSetBuilderService

    pool(client, default_floating_pool=True)
    body = create(
        client,
        ("compute", "sets"),
        project_id=str(uuid.uuid4()),
        cores=1,
        ram=1024,
        replicas=1,
        disk_spec={"kind": "root_disk", "image": "ubuntu_24.04"},
        default_network={"floating_ip": True},
    )
    node_set = stored(sets.NodeSet, body["uuid"])
    node = node_set.gen_nodes(project_id=nc.NODE_SET_PROJECT)[0]
    node.insert()
    port = node.ports[0]
    address = catalog.Address.objects.get_one(
        filters={"port": filters.EQ(port["uuid"])}
    )
    service = object.__new__(NodeSetBuilderService)
    service._project_id = nc.NODE_SET_PROJECT
    infra = mock.Mock(infra_objects=[(node, node)])
    service.actualize_infra(node_set, infra)
    node_set.update()
    response = client.get(resource(client, "compute", "sets", body["uuid"])).json()
    assert response["nodes"][str(node.uuid)] == {
        "ipv4": port["ipv4"],
        "floating_ip": address.address,
    }
    with pytest.raises(exceptions.BadRequestError):
        client.put(
            resource(client, "network", "ports", port["uuid"]), json={"node": None}
        )
    with pytest.raises(exceptions.BadRequestError):
        client.put(
            resource(client, "network", "ports", port["uuid"]),
            json={"owner_node": None},
        )
    with pytest.raises(exceptions.BadRequestError):
        client.delete(resource(client, "network", "ports", port["uuid"]))
    with pytest.raises(exceptions.BadRequestError):
        client.put(
            resource(client, "network", "catalog", "addresses", str(address.uuid)),
            json={"owner_port": None},
        )
    previous_update = stored(sets.NodeSet, body["uuid"]).updated_at
    address.port = None
    address.update()
    assert stored(sets.NodeSet, body["uuid"]).updated_at > previous_update
    service.actualize_infra(node_set, infra)
    assert node_set.nodes[str(node.uuid)] == {"ipv4": port["ipv4"]}
    with pytest.raises(exceptions.BadRequestError):
        create(
            client,
            ("network", "catalog", "addresses"),
            project_id=body["project_id"],
            kind="floating",
            subnet=str(address.subnet),
            port=port["uuid"],
        )
    address_url = resource(client, "network", "catalog", "addresses", str(address.uuid))
    with pytest.raises(exceptions.BadRequestError):
        client.put(address_url, json={"port": port["uuid"]})
    address.port = uuid.UUID(port["uuid"])
    address.update()
    with pytest.raises(exceptions.BadRequestError):
        client.delete(address_url)


def test_ownership_migration_preserves_independent_ports(client):
    from pathlib import Path
    import runpy

    from restalchemy.common import contexts

    project = uuid.uuid4()
    node = guest(client, project)
    owned = node["ports"][0]["uuid"]
    independent = create(
        client, ("network", "ports"), project_id=str(project), node=node["uuid"]
    )
    migration = runpy.run_path(
        str(Path(__file__).parents[5] / "migrations/0011-network-ownership-d4f9031a.py")
    )["migration_step"]
    with contexts.Context().session_manager() as session:
        migration.downgrade(session)
        migration.upgrade(session)
    assert stored(compute.Port, owned).owner_node == uuid.UUID(node["uuid"])
    assert stored(compute.Port, independent["uuid"]).owner_node is None


def test_node_ownership_does_not_make_an_address_pool_placeable(client):
    public = pool(client)
    with pytest.raises(exceptions.BadRequestError):
        guest(client, uuid.uuid4(), default_network={"subnet": public["uuid"]})


def test_releasing_foreign_declared_port_preserves_placement(client):
    tenant, service = uuid.uuid4(), uuid.uuid4()
    network = create(
        client,
        ("network", "networks"),
        project_id=str(tenant),
        driver_spec={"driver": "ovs_evpn"},
    )
    subnet = create(
        client,
        ("network", "subnets"),
        project_id=str(tenant),
        network=network["uuid"],
        cidr="10.41.0.0/24",
    )
    node = guest(
        client, service, interfaces=[{"kind": "subnet", "subnet": subnet["uuid"]}]
    )
    port = compute.Port(
        project_id=service,
        node=uuid.UUID(node["uuid"]),
        owner_node=uuid.UUID(node["uuid"]),
        subnet=uuid.UUID(subnet["uuid"]),
    )
    port.insert()
    url = resource(client, "network", "ports", str(port.uuid))
    client.put(url, json={"owner_node": None})
    client.put(url, json={"node": None})
    assert stored(compute.Port, port.uuid).owner_node is None
    with pytest.raises(exceptions.BadRequestError):
        create(
            client,
            ("network", "ports"),
            project_id=str(service),
            node=node["uuid"],
            subnet=subnet["uuid"],
        )


def test_ownership_migration_preserves_detached_legacy_flat_access(client):
    from pathlib import Path
    import runpy

    from restalchemy.common import contexts

    port = legacy_flat_port(uuid.uuid4())
    migration = runpy.run_path(
        str(Path(__file__).parents[5] / "migrations/0011-network-ownership-d4f9031a.py")
    )["migration_step"]
    with contexts.Context().session_manager() as session:
        migration.downgrade(session)
        session.execute(
            "UPDATE compute_ports SET provenance = 'generated', legacy_flat_placement = FALSE WHERE uuid = %s",
            (str(port.uuid),),
        )
        migration.upgrade(session)
    saved = stored(compute.Port, port.uuid)
    saved.name = "preserved-legacy-port"
    saved.update()
    assert saved.owner_node is None


@pytest.mark.parametrize("change", ["detach", "release"])
def test_node_delete_can_race_with_primary_port_release(client, monkeypatch, change):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    from restalchemy.storage.sql import orm

    node = guest(client, uuid.uuid4())
    port_id = node["ports"][0]["uuid"]
    held, deleting = threading.Event(), threading.Event()
    operation = threading.local()
    original = orm.ObjectCollection.get_one

    def get_one(collection, *args, **kwargs):
        port_lock = collection.model_cls is compute.Port and kwargs.get("locked")
        if port_lock and getattr(operation, "name", None) == "delete":
            deleting.set()
        result = original(collection, *args, **kwargs)
        if port_lock and getattr(operation, "name", None) == "detach":
            held.set()
            assert deleting.wait(5)
        return result

    monkeypatch.setattr(orm.ObjectCollection, "get_one", get_one)

    def detach():
        operation.name = "detach"
        port = stored(compute.Port, port_id)
        if change == "detach":
            port.node = None
        else:
            port.owner_node = None
        port.update()

    def delete():
        operation.name = "delete"
        assert held.wait(5)
        stored(compute.Node, node["uuid"]).delete()

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(detach), workers.submit(delete)]
        for future in futures:
            future.result(timeout=15)
    assert stored(compute.Node, node["uuid"]) is None
    assert stored(compute.Port, port_id).owner_node is None


def test_node_delete_can_race_with_fip_ownership_release(client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    from restalchemy.storage.sql import orm

    pool(client, default_floating_pool=True)
    node = guest(client, uuid.uuid4(), default_network={"floating_ip": True})
    port_id = node["ports"][0]["uuid"]
    address = catalog.Address.objects.get_one(filters={"port": filters.EQ(port_id)})
    held, deleting = threading.Event(), threading.Event()
    operation = threading.local()
    original = orm.ObjectCollection.get_one

    def get_one(collection, *args, **kwargs):
        result = original(collection, *args, **kwargs)
        if kwargs.get("locked"):
            if (
                collection.model_cls is catalog.Address
                and getattr(operation, "name", None) == "release"
            ):
                held.set()
                assert deleting.wait(5)
            if (
                collection.model_cls is compute.Port
                and getattr(operation, "name", None) == "delete"
            ):
                deleting.set()
        return result

    monkeypatch.setattr(orm.ObjectCollection, "get_one", get_one)

    def release():
        operation.name = "release"
        released = stored(catalog.Address, address.uuid)
        released.owner_port = None
        released.update()

    def delete():
        operation.name = "delete"
        assert held.wait(5)
        stored(compute.Node, node["uuid"]).delete()

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(release), workers.submit(delete)]
        for future in futures:
            future.result(timeout=15)
    assert stored(compute.Node, node["uuid"]) is None
    assert stored(compute.Port, port_id) is None
    assert stored(catalog.Address, address.uuid).owner_port is None
    assert stored(catalog.Address, address.uuid).port is None
