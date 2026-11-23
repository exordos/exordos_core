# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0

import json
from types import SimpleNamespace
import uuid

from bazooka import exceptions
import pytest
from restalchemy.dm import filters

from exordos_core.cloud.specs import CoreHypervisorSpec
from exordos_core.compute.dm import models
from exordos_core.tests.functional.restapi.network.test_sdn_contract_api import (
    collection,
)
from exordos_core.tests.functional.restapi.network.test_sdn_contract_api import (
    create as _create,
)
from exordos_core.tests.functional.restapi.network.test_sdn_contract_api import resource
from exordos_core.tests.functional.restapi.network.test_subnet_placement import (
    client as client,
)
from exordos_core.tests.functional.restapi.network.test_subnet_placement import local
from exordos_core.tests.functional.restapi.network.test_subnet_placement import network
from exordos_core.tests.functional.restapi.network.test_subnet_placement import subnet


def create(*args, **kwargs):
    try:
        return _create(*args, **kwargs)
    except exceptions.BadRequestError as error:
        pytest.fail(error.cause.response.text)


def test_remote_core_credentials_remain_on_controller(client):
    remote_project, remote_hypervisor = str(uuid.uuid4()), str(uuid.uuid4())
    value = create(
        client,
        ("compute", "hypervisors"),
        driver_spec={
            "kind": "core",
            "api_url": "https://core.example",
            "project_id": remote_project,
            "hypervisors": [remote_hypervisor],
            "auth": {"username": "operator", "password": "provider-secret"},
        },
    )
    assert "provider-secret" not in str(value)
    assert value["driver_spec"]["kind"] == "core"
    stored = models.MachinePool.objects.get_one(
        filters={"uuid": filters.EQ(value["uuid"])}
    )
    assert isinstance(stored.driver_spec, CoreHypervisorSpec)
    assert stored.driver_spec.auth["password"] == "provider-secret"
    from exordos_core.compute.pool.dm.models import Pool

    projection = Pool.objects.get_one(filters={"uuid": filters.EQ(value["uuid"])})
    assert "provider-secret" not in str(projection.get_ua_all_and_target_values())


@pytest.fixture
def reserved(client):
    host = local(client)
    root = network(client)
    segment = subnet(client, root, host)
    port = create(client, ("network", "ports"), subnet=segment["uuid"])
    return client, root, port


def node_body(root, ports):
    return {
        "name": "provider-node",
        "cores": 1,
        "ram": 512,
        "default_network": {"network": root["uuid"]},
        "ports": ports,
        "disk_spec": {
            "kind": "root_disk",
            "size": 8,
            "image": "https://images.example/os.raw.zst",
        },
    }


@pytest.fixture
def provider_machine(reserved):
    from gcl_sdk.agents.universal.drivers import pool
    from gcl_sdk.agents.universal.drivers.core_pool import CorePoolDriver
    from gcl_sdk.clients.http.base import CollectionBaseClient
    from gcl_sdk.cloud.drivers.core import CoreConfig
    from gcl_sdk.cloud.drivers.core import CoreDriver

    client, root, port = reserved
    connection, upstream, pool_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    api_url = collection(client, "compute", "nodes").split("/v1/")[0]
    remote = CoreDriver(
        CoreConfig(
            connection,
            api_url,
            uuid.UUID(root["project_id"]),
            (uuid.uuid4(),),
            {"username": "unused", "password": "unused"},
        ),
        client=CollectionBaseClient(
            api_url,
            http_client=client,
            auth=SimpleNamespace(get_auth_header=lambda: {}, authenticate=lambda: None),
        ),
    )
    machine = pool.Machine(
        uuid=upstream,
        node=upstream,
        project_id=uuid.UUID(root["project_id"]),
        pool=pool_id,
        name="provider-node",
        cores=1,
        ram=512,
    )
    body = node_body(root, [port["uuid"]])
    body.update(
        uuid=str(remote.resource_id("node", upstream)),
        description=remote.marker("node", upstream),
        bootstrap={
            "description": remote.marker("config", upstream),
            "path": "/root/provider.py",
            "content": "# exordos-provider "
            + json.dumps({"machine": machine.dump_to_simple_view(), "volumes": []})
            + "\n# private-key-sentinel\n",
            "command": "/usr/bin/python3 /root/provider.py",
        },
    )
    node = create(client, ("compute", "nodes"), **body)
    adapter = CorePoolDriver(
        pool.MachinePool(
            uuid=pool_id, driver_spec=pool.CloudPoolDriverSpec(connection=connection)
        ),
        remote,
    )
    return client, node, remote.resource_id("config", upstream), remote, adapter


def test_bootstrap_cannot_be_deleted_separately_from_its_node(provider_machine):
    from exordos_core.cloud import core_links
    from exordos_core.config.dm.models import Config

    client, node, config_id, remote, adapter = provider_machine
    assert len(adapter.list_machines()) == 1
    with pytest.raises(exceptions.ConflictError):
        client.delete(resource(client, "config", "configs", str(config_id)))
    assert len(adapter.list_machines()) == 1
    assert core_links.gateways(remote, SimpleNamespace(uuid=uuid.uuid4())) == []
    client.delete(resource(client, "compute", "nodes", node["uuid"]))
    assert (
        Config.objects.get_one_or_none(filters={"uuid": filters.EQ(config_id)}) is None
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"body": {"kind": "text", "content": "changed"}},
        {"target": {"kind": "node", "node": str(uuid.uuid4())}},
        {"description": "changed"},
        {"path": "/tmp/changed"},
        {"on_change": {"kind": "no_action"}},
        {"mode": "0644"},
    ],
)
def test_bootstrap_cannot_be_updated_through_config_api(provider_machine, updates):
    client, _, config_id, _, adapter = provider_machine
    with pytest.raises(exceptions.ConflictError) as error:
        client.put(resource(client, "config", "configs", str(config_id)), json=updates)
    assert "private-key-sentinel" not in error.value.cause.response.text
    assert len(adapter.list_machines()) == 1


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_bootstrap_restrictions_preserve_config_permissions(
    provider_machine, auth_test1_user, user_api_client, operation
):
    _, _, identity, _, adapter = provider_machine
    reader = user_api_client(auth_test1_user, permissions=["config.config.read"])
    path = resource(reader, "config", "configs", str(identity))
    assert reader.get(path).status_code == 200
    with pytest.raises(exceptions.ForbiddenError):
        if operation == "update":
            reader.put(path, json={"name": "changed"})
        else:
            reader.delete(path)
    assert len(adapter.list_machines()) == 1


def test_config_update_permission_does_not_require_read(
    provider_machine, auth_test1_user, user_api_client
):
    client, node, identity, _, adapter = provider_machine
    ordinary = client.post(
        collection(client, "config", "configs"),
        json={
            "project_id": node["project_id"],
            "path": "/tmp/ordinary-config",
            "target": {"kind": "node", "node": node["uuid"]},
            "body": {"kind": "text", "content": "original"},
        },
    ).json()
    writer = user_api_client(auth_test1_user, permissions=["config.config.update"])
    path = resource(writer, "config", "configs", ordinary["uuid"])
    with pytest.raises(exceptions.ForbiddenError):
        writer.get(path)
    updated = writer.put(path, json={"body": {"kind": "text", "content": "updated"}})
    assert updated.status_code == 200
    assert updated.json()["body"]["content"] == "updated"
    with pytest.raises(exceptions.ConflictError):
        writer.put(
            resource(writer, "config", "configs", str(identity)),
            json={"name": "changed"},
        )
    assert len(adapter.list_machines()) == 1


@pytest.mark.parametrize("operation", ["body", "target", "delete"])
def test_bootstrap_is_protected_on_direct_model_writes(provider_machine, operation):
    from restalchemy.storage import exceptions as storage_exceptions

    from exordos_core.config.dm import models as config_models

    _, _, identity, _, adapter = provider_machine
    config = config_models.Config.objects.get_one(
        filters={"uuid": filters.EQ(identity)}
    )
    before = config.dump_to_simple_view()
    with pytest.raises(storage_exceptions.ConflictRecords):
        if operation == "delete":
            config.delete()
        else:
            if operation == "body":
                config.body = config_models.TextBodyConfig(content="changed")
            else:
                config.target = type(config.target)(node=uuid.uuid4())
            config.update()
    assert (
        config_models.Config.objects.get_one(
            filters={"uuid": filters.EQ(identity)}
        ).dump_to_simple_view()
        == before
    )
    assert len(adapter.list_machines()) == 1


def test_bootstrap_status_can_advance_and_orphan_can_be_collected(provider_machine):
    from exordos_core.config.dm.models import Config
    from exordos_core.config.service import ConfigServiceBuilder

    _, node, identity, _, _ = provider_machine
    config = Config.objects.get_one(filters={"uuid": filters.EQ(identity)})
    config.status = "IN_PROGRESS"
    config.update()
    config.update(force=True)
    assert (
        Config.objects.get_one(filters={"uuid": filters.EQ(identity)}).status
        == "IN_PROGRESS"
    )
    models.Node.objects.get_one(filters={"uuid": filters.EQ(node["uuid"])}).delete()
    ConfigServiceBuilder()._actualize_new_config(config, [])
    assert (
        Config.objects.get_one_or_none(filters={"uuid": filters.EQ(identity)}) is None
    )


def test_node_creation_claims_reserved_ports_atomically(reserved):
    client, root, port = reserved
    node = create(client, ("compute", "nodes"), **node_body(root, [port["uuid"]]))
    ports = client.get(
        collection(client, "network", "ports"), params={"node": node["uuid"]}
    ).json()
    assert len(ports) == 1
    assert ports[0]["uuid"] == port["uuid"]
    assert ports[0]["index"] == 0
    assert ports[0]["owner_node"] == node["uuid"]


def test_bootstrap_and_node_share_creation_and_cleanup_transaction(reserved):
    from exordos_core.config.dm.models import Config

    client, root, port = reserved
    body = node_body(root, [port["uuid"]])
    body["bootstrap"] = {
        "description": "provider-owned",
        "path": "/root/workload-bootstrap.py",
        "content": "private-bootstrap-sentinel",
        "command": "/usr/bin/python3 /root/workload-bootstrap.py",
    }
    node = create(client, ("compute", "nodes"), **body)
    key = uuid.uuid5(uuid.UUID(node["uuid"]), "provider-bootstrap")
    config = Config.objects.get_one(filters={"uuid": filters.EQ(key)})
    assert config.target.node == uuid.UUID(node["uuid"])
    assert config.mode == "0600"
    assert config.body.content == "private-bootstrap-sentinel"
    assert "private-bootstrap-sentinel" not in str(node)
    assert (
        "private-bootstrap-sentinel"
        not in client.get(resource(client, "compute", "nodes", node["uuid"])).text
    )
    assert (
        "private-bootstrap-sentinel"
        not in client.get(resource(client, "config", "configs", str(key))).text
    )
    assert (
        "private-bootstrap-sentinel"
        not in client.get(collection(client, "config", "configs")).text
    )
    client.delete(resource(client, "compute", "nodes", node["uuid"]))
    assert Config.objects.get_one_or_none(filters={"uuid": filters.EQ(key)}) is None


def test_claiming_attached_port_rolls_back_new_node(reserved):
    client, root, port = reserved
    create(client, ("compute", "nodes"), **node_body(root, [port["uuid"]]))
    before = client.get(collection(client, "compute", "nodes")).json()
    with pytest.raises(exceptions.ConflictError):
        create(client, ("compute", "nodes"), **node_body(root, [port["uuid"]]))
    assert client.get(collection(client, "compute", "nodes")).json() == before


def test_claiming_foreign_project_port_is_rejected_without_mutation(reserved):
    client, root, port = reserved
    foreign = str(uuid.uuid4())
    host = local(client, project_id=foreign)
    foreign_root = network(client, project_id=foreign, name="foreign")
    foreign_subnet = subnet(client, foreign_root, host, project_id=foreign)
    port = create(
        client, ("network", "ports"), subnet=foreign_subnet["uuid"], project_id=foreign
    )
    with pytest.raises(exceptions.NotFoundError):
        create(client, ("compute", "nodes"), **node_body(root, [port["uuid"]]))
    assert (
        models.Port.objects.get_one(filters={"uuid": filters.EQ(port["uuid"])}).node
        is None
    )


def test_node_can_claim_secondary_port_from_another_logical_network(reserved):
    client, root, primary = reserved
    host = local(client)
    other = create(client, ("network", "networks"), cidr="10.82.0.0/16")
    other_subnet = subnet(client, other, host)
    secondary = create(client, ("network", "ports"), subnet=other_subnet["uuid"])
    node = create(
        client,
        ("compute", "nodes"),
        **node_body(root, [primary["uuid"], secondary["uuid"]]),
    )
    observed = client.get(
        collection(client, "network", "ports"), params={"node": node["uuid"]}
    ).json()
    assert {(p["uuid"], p["index"]) for p in observed} == {
        (primary["uuid"], 0),
        (secondary["uuid"], 1),
    }


def test_unrelated_config_with_reserved_uuid_does_not_block_node_delete(reserved):
    from exordos_core.config.dm.models import Config

    client, root, port = reserved
    node = create(client, ("compute", "nodes"), **node_body(root, [port["uuid"]]))
    identity = uuid.uuid5(uuid.UUID(node["uuid"]), "provider-bootstrap")
    unrelated_target = uuid.uuid4()
    client.post(
        collection(client, "config", "configs"),
        json={
            "uuid": str(identity),
            "project_id": root["project_id"],
            "path": "/root/unrelated",
            "target": {"kind": "node", "node": str(unrelated_target)},
            "body": {"kind": "text", "content": "unrelated"},
        },
    )
    client.delete(resource(client, "compute", "nodes", node["uuid"]))
    assert (
        Config.objects.get_one(filters={"uuid": filters.EQ(identity)}).target.node
        == unrelated_target
    )
    path = resource(client, "config", "configs", str(identity))
    assert (
        client.put(
            path, json={"body": {"kind": "text", "content": "updated"}}
        ).status_code
        == 200
    )
    assert client.delete(path).status_code == 204


def test_invalid_bootstrap_does_not_echo_content_in_error(reserved):
    client, root, port = reserved
    body = node_body(root, [port["uuid"]])
    body["bootstrap"] = "private-bootstrap-sentinel"
    with pytest.raises(exceptions.BadRequestError) as error:
        _create(client, ("compute", "nodes"), **body)
    assert "private-bootstrap-sentinel" not in error.value.cause.response.text


def test_bootstrap_delete_authorization_rolls_back_node_and_config(reserved):
    from exordos_core.config.dm.models import Config
    from exordos_core.user_api.compute.dm.models import Node

    client, root, port = reserved
    body = node_body(root, [port["uuid"]])
    body["bootstrap"] = {
        "description": "provider-owned",
        "path": "/root/provider.py",
        "content": "private-key",
        "command": "/usr/bin/python3 /root/provider.py",
    }
    created = create(client, ("compute", "nodes"), **body)
    node = Node.objects.get_one(filters={"uuid": filters.EQ(created["uuid"])})
    config = uuid.uuid5(node.uuid, "provider-bootstrap")

    def denied():
        raise PermissionError("Config deletion forbidden")

    with pytest.raises(PermissionError):
        node.delete(authorize_bootstrap=denied)
    assert Config.objects.get_one(filters={"uuid": filters.EQ(config)}) is not None
    assert Node.objects.get_one(filters={"uuid": filters.EQ(node.uuid)}) is not None
    assert (
        models.Port.objects.get_one(filters={"uuid": filters.EQ(port["uuid"])}).node
        == node.uuid
    )
    client.delete(resource(client, "compute", "nodes", created["uuid"]))
    assert Config.objects.get_one_or_none(filters={"uuid": filters.EQ(config)}) is None
