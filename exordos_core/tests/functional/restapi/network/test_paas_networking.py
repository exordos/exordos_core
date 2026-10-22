# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0.

import importlib.util
from pathlib import Path
import uuid

from bazooka import exceptions
from gcl_sdk.agents.universal import utils as ua_utils
from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.infra.dm import models as sdk_models
import pytest
from restalchemy.dm import filters
from restalchemy.storage.sql import engines

from exordos_core.common import config
from exordos_core.compute import constants as nc
from exordos_core.compute.dm import models as compute
from exordos_core.compute.node_set.dm import models as sets
from exordos_core.tests.functional.restapi.network.test_default_network import (
    legacy_flat_port,
)
from exordos_core.tests.functional.restapi.network.test_sdn_contract_api import (
    collection,
)
from exordos_core.tests.functional.restapi.network.test_sdn_contract_api import create
from exordos_core.tests.functional.restapi.network.test_sdn_contract_api import (
    node_ports,
)
from exordos_core.tests.functional.restapi.network.test_sdn_contract_api import resource
from exordos_core.user_api.network.dm import models as catalog

PERMISSION = "network.port.use_foreign_subnet"


@pytest.fixture
def client(user_api_client, auth_user_admin, user_api):
    yield user_api_client(auth_user_admin)
    user_api._restore_seed_snapshot()


def subnet(client, project_id, **kwargs):
    network = create(
        client,
        ("network", "networks"),
        project_id=str(project_id),
        driver_spec={"driver": "ovs_evpn"},
    )
    return create(
        client,
        ("network", "subnets"),
        project_id=str(project_id),
        network=network["uuid"],
        cidr="10.42.0.0/24",
        **kwargs,
    )


def nodeset(client, project_id, **kwargs):
    return create(
        client,
        ("compute", "sets"),
        project_id=str(project_id),
        cores=1,
        ram=1024,
        replicas=1,
        disk_spec={"kind": "root_disk", "image": "ubuntu_24.04"},
        **kwargs,
    )


def replica(body):
    parent = sets.NodeSet.objects.get_one(filters={"uuid": filters.EQ(body["uuid"])})
    node = parent.gen_nodes(project_id=nc.NODE_SET_PROJECT)[0]
    node.insert()
    return node


def test_foreign_private_port_requires_special_permission(
    client, user_api_client, auth_test1_user
):
    private = subnet(client, uuid.uuid4())
    limited = user_api_client(auth_test1_user, permissions=["network.port.create"])
    body = {"project_id": str(uuid.UUID(int=0)), "subnet": private["uuid"]}
    with pytest.raises(exceptions.ForbiddenError):
        limited.post(collection(limited, "network", "ports"), json=body)
    permitted = user_api_client(
        auth_test1_user, permissions=["network.port.create", PERMISSION]
    )
    port = create(permitted, ("network", "ports"), **body)
    assert port["project_id"] == body["project_id"]
    assert port["subnet"] == private["uuid"]


def test_service_nodeset_uses_customer_default_and_preserves_ownership(
    client, user_api_client, auth_test1_user
):
    customer = uuid.uuid4()
    service = uuid.UUID(int=0)
    permitted = user_api_client(
        auth_test1_user, permissions=["compute.node_set.create", PERMISSION]
    )
    body = nodeset(permitted, service, default_network={"project_id": str(customer)})
    node = replica(body)
    port = node_ports(client, node)[0]
    network = compute.Network.objects.get_one(
        filters={"uuid": filters.EQ(port["network"])}
    )
    assert network.project_id == customer
    assert network.is_default
    assert port["project_id"] == str(service)
    assert node.project_id == nc.NODE_SET_PROJECT
    assert body["network_subnet"] == port["subnet"]


def test_nodeset_selection_requires_permission_before_replicas(
    client, user_api_client, auth_test1_user
):
    limited = user_api_client(auth_test1_user, permissions=["compute.node_set.create"])
    with pytest.raises(exceptions.ForbiddenError):
        nodeset(
            limited, uuid.UUID(int=0), default_network={"project_id": str(uuid.uuid4())}
        )


def test_nodeset_pins_default_for_replacement_replicas(client):
    customer = uuid.uuid4()
    body = nodeset(client, uuid.uuid4(), default_network={"project_id": str(customer)})
    first = replica(body)
    original = node_ports(client, first)[0]
    first.delete()
    with pytest.raises(exceptions.ConflictError):
        client.delete(resource(client, "network", "subnets", original["subnet"]))
    network = compute.Network.objects.get_one(
        filters={"uuid": filters.EQ(original["network"])}
    )
    network.is_default = False
    network.update()
    second = replica(body)
    assert node_ports(client, second)[0]["subnet"] == original["subnet"]


def test_customer_flat_default_survives_compatibility_switch(client):
    customer = uuid.uuid4()
    config.CONF.set_override("legacy_flat_default", True, group="network")
    try:
        existing = legacy_flat_port(customer)
        body = nodeset(
            client, uuid.uuid4(), default_network={"project_id": str(customer)}
        )
        config.CONF.set_override("legacy_flat_default", False, group="network")
        node = replica(body)
        port = node_ports(client, node)[0]
        assert port["subnet"] == str(existing.subnet)
    finally:
        config.CONF.clear_override("legacy_flat_default", group="network")


def test_replica_security_group_is_bound_with_port(client):
    service = uuid.uuid4()
    group = create(
        client, ("network", "catalog", "security_groups"), project_id=str(service)
    )
    body = nodeset(
        client,
        service,
        default_network={
            "project_id": str(uuid.uuid4()),
            "security_groups": [group["uuid"]],
        },
    )
    node = replica(body)
    port = node_ports(client, node)[0]
    assert port["security_groups"] == [group["uuid"]]
    assert port["enforce_security_group"]
    bindings = catalog.PortSecurityGroup.objects.get_all(
        filters={"port": filters.EQ(port["uuid"])}
    )
    assert len(bindings) == 1
    with pytest.raises(exceptions.ConflictError):
        client.delete(resource(client, "network", "subnets", port["subnet"]))


def test_direct_model_port_cannot_bypass_foreign_subnet_access(client):
    private = subnet(client, uuid.uuid4())
    port = compute.Port(project_id=uuid.uuid4(), subnet=uuid.UUID(private["uuid"]))
    with pytest.raises(Exception, match="accessible|another project"):
        port.insert()


def test_replica_cannot_substitute_another_subnet(client):
    private = subnet(client, uuid.uuid4())
    other = subnet(client, uuid.uuid4())
    body = nodeset(client, uuid.uuid4(), default_network={"subnet": private["uuid"]})
    parent = sets.NodeSet.objects.get_one(filters={"uuid": filters.EQ(body["uuid"])})
    node = parent.gen_nodes(project_id=nc.NODE_SET_PROJECT)[0]
    node.default_network = {"subnet": other["uuid"]}
    node.insert()
    assert node_ports(client, node)[0]["subnet"] == private["uuid"]


def test_customer_selector_cannot_name_another_customers_private_subnet(client):
    private = subnet(client, uuid.uuid4())
    with pytest.raises(exceptions.BadRequestError):
        nodeset(
            client,
            uuid.uuid4(),
            default_network={
                "project_id": str(uuid.uuid4()),
                "subnet": private["uuid"],
            },
        )


def test_customer_node_cannot_inherit_foreign_nodeset_network(
    client, user_api_client, auth_test1_p1_user
):
    parent = nodeset(
        client, uuid.uuid4(), default_network={"project_id": str(uuid.uuid4())}
    )
    scoped = user_api_client(
        auth_test1_p1_user,
        permissions=["compute.node.create"],
        project_id=auth_test1_p1_user.project_id,
    )
    with pytest.raises(exceptions.NotFoundError):
        create(
            scoped,
            ("compute", "nodes"),
            project_id=str(auth_test1_p1_user.project_id),
            cores=1,
            ram=1024,
            node_set=parent["uuid"],
            disk_spec={"kind": "root_disk", "image": "pg.raw"},
        )
    stored = sets.NodeSet.objects.get_one(filters={"uuid": filters.EQ(parent["uuid"])})
    node = stored.gen_nodes(project_id=uuid.UUID(str(auth_test1_p1_user.project_id)))[0]
    with pytest.raises(Exception, match="NodeSet belongs to another project"):
        node.insert()


def test_migration_pins_existing_nodesets_without_moving_ports(client):
    body = nodeset(client, uuid.uuid4())
    node = replica(body)
    original = node_ports(client, node)[0]
    path = Path(__file__).parents[5] / "migrations/0011-paas-networking-485e71.py"
    spec = importlib.util.spec_from_file_location("paas_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engines.engine_factory.get_engine().session_manager() as session:
        module.migration_step.downgrade(session)
        module.migration_step.upgrade(session)
    parent = compute.NodeSet.objects.get_one(filters={"uuid": filters.EQ(body["uuid"])})
    assert str(parent.network_subnet) == original["subnet"]
    assert node_ports(client, node)[0]["uuid"] == original["uuid"]


def test_missing_group_rolls_back_node_and_port(client):
    service = uuid.uuid4()
    body = nodeset(
        client,
        service,
        default_network={
            "project_id": str(uuid.uuid4()),
            "security_groups": [str(uuid.uuid4())],
        },
    )
    parent = sets.NodeSet.objects.get_one(filters={"uuid": filters.EQ(body["uuid"])})
    node = parent.gen_nodes(project_id=nc.NODE_SET_PROJECT)[0]
    with pytest.raises(Exception, match="does not exist|Nothing is found"):
        node.insert()
    assert (
        compute.Node.objects.get_one_or_none(filters={"uuid": filters.EQ(node.uuid)})
        is None
    )
    assert compute.Port.objects.get_all(filters={"node": filters.EQ(node.uuid)}) == []


def test_scoped_nodeset_update_keeps_project_boundary(
    client, user_api_client, auth_test1_p1_user
):
    scoped = user_api_client(
        auth_test1_p1_user,
        permissions=[
            "compute.node_set.create",
            "compute.node_set.read",
            "compute.node_set.update",
        ],
        project_id=auth_test1_p1_user.project_id,
    )
    body = nodeset(scoped, auth_test1_p1_user.project_id)
    with pytest.raises(exceptions.ForbiddenError):
        scoped.put(
            resource(scoped, "compute", "sets", body["uuid"]),
            json={"project_id": str(uuid.uuid4())},
        )


def test_network_subnet_observation_cannot_be_forged(
    client, user_api_client, auth_test1_user
):
    private = subnet(client, uuid.uuid4())
    limited = user_api_client(auth_test1_user, permissions=["compute.node_set.create"])
    body = nodeset(limited, uuid.UUID(int=0), network_subnet=private["uuid"])
    assert body.get("network_subnet") != private["uuid"]
    assert node_ports(client, replica(body))[0]["subnet"] != private["uuid"]


def test_customer_flat_port_keeps_legacy_security_behavior(client):
    customer, service = uuid.uuid4(), uuid.uuid4()
    previous = legacy_flat_port(customer)
    group = create(
        client, ("network", "catalog", "security_groups"), project_id=str(service)
    )
    body = nodeset(
        client,
        service,
        default_network={
            "project_id": str(customer),
            "security_groups": [group["uuid"]],
        },
    )
    node = replica(body)
    port = node_ports(client, node)[0]
    assert port["subnet"] == str(previous.subnet)
    assert port["security_groups"] == []
    assert not port["enforce_security_group"]


def test_sdk_policy_and_nodeset_round_trip_through_core(client):
    service, customer = uuid.uuid4(), uuid.uuid4()
    group = sdk_models.SecurityGroup(
        uuid=uuid.uuid4(), project_id=service, name="database"
    )
    rule = sdk_models.SecurityGroupRule(
        uuid=uuid.uuid4(),
        project_id=service,
        security_group=group.uuid,
        direction="ingress",
        protocol="tcp",
        port=5010,
        remote_group_id=group.uuid,
    )
    nodes = sdk_models.NodeSet(
        uuid=uuid.uuid4(),
        project_id=service,
        name="database",
        cores=1,
        ram=1024,
        disk_spec=sdk_models.SetRootDiskSpec(image="pg.raw"),
        default_network={
            "project_id": str(customer),
            "security_groups": [str(group.uuid)],
        },
    )
    for obj, path in (
        (group, ("network", "catalog", "security_groups")),
        (rule, ("network", "catalog", "security_group_rules")),
        (nodes, ("compute", "sets")),
    ):
        target = obj.to_ua_resource()
        actual = client.post(collection(client, *path), json=target.value).json()
        observed = ua_models.Resource.from_value(
            actual, target.kind, ua_utils.value_shape(target.value)
        )
        assert observed.hash == target.hash
    node = replica({"uuid": str(nodes.uuid)})
    assert node_ports(client, node)[0]["security_groups"] == [str(group.uuid)]
