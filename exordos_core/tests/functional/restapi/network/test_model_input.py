# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0

import uuid

from gcl_sdk.agents.universal.clients.backend.db import DatabaseBackendClient
from gcl_sdk.agents.universal.clients.backend.db import ModelSpec
from gcl_sdk.agents.universal.dm import models as ua
from gcl_sdk.agents.universal.storage.fs import TargetFieldsFileStorage
import pytest
from restalchemy.dm import filters
from restalchemy.storage import exceptions as storage_exc

from exordos_core.common import exceptions
from exordos_core.compute.dm import models as compute
from exordos_core.user_api.network.dm import models as catalog


@pytest.fixture
def backend(user_api, tmp_path):
    project = uuid.UUID(int=0)
    network = compute.Network.restore_from_simple_view(
        project_id=str(project), driver_spec={"driver": "ovs_evpn"}
    )
    network.insert()
    subnet = compute.Subnet.restore_from_simple_view(
        project_id=str(project), network=str(network.uuid), cidr="10.214.0.0/24"
    )
    subnet.insert()
    models = {
        "node": compute.Node,
        "nodeset": compute.NodeSet,
        "port": compute.Port,
        "address": catalog.Address,
        "network": compute.Network,
    }
    client = DatabaseBackendClient(
        [
            ModelSpec(model, kind, filters={"project_id": filters.EQ(str(project))})
            for kind, model in models.items()
        ],
        TargetFieldsFileStorage(str(tmp_path / "targets.json")),
    )
    node = {
        "cores": 1,
        "ram": 1024,
        "disk_spec": {"kind": "root_disk", "image": "ubuntu_24.04"},
        "default_network": {},
    }
    values = {
        "node": node,
        "nodeset": {**node, "replicas": 1},
        "port": {"subnet": str(subnet.uuid)},
        "address": {"subnet": str(subnet.uuid), "kind": "reservation"},
        "network": {"driver_spec": {"driver": "ovs_evpn"}},
    }
    return client, values


def request(kind, value):
    return ua.Resource.from_value(value, kind, frozenset(value))


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize(
    "kind,patch",
    [
        ("node", {"default_network": {"ipv4": "10.214.0.10"}}),
        ("nodeset", {"default_network": {"target_ipv4": "10.214.0.10"}}),
        ("port", {"index": True}),
        ("port", {"target_mac": "01:00:00:00:00:01"}),
        ("port", {"mac": "01:00:00:00:00:01"}),
        ("port", {"target_ipv4": "203.0.113.1"}),
        ("address", {"kind": "reservation", "port": str(uuid.uuid4())}),
        ("address", {"address": "203.0.113.1"}),
        ("network", {"driver_spec": {"driver": "ovs_evpn", "mtu": True}}),
    ],
)
def test_model_rejects_invalid_state_without_persisting(
    backend, operation, kind, patch
):
    client, values = backend
    value = {
        "uuid": str(uuid.uuid4()),
        "project_id": str(uuid.UUID(int=0)),
        **values[kind],
    }
    original = request(kind, value)
    if operation == "update":
        client.create(original)
        before = client.get(original).dump_to_simple_view()
    invalid = request(kind, {**value, **patch})

    with pytest.raises((exceptions.ValidateException, storage_exc.ConflictRecords)):
        getattr(client, operation)(invalid)

    if operation == "update":
        assert client.get(original).dump_to_simple_view() == before
    else:
        model = client._model_spec_map[kind].model
        assert (
            model.objects.get_one_or_none(filters={"uuid": filters.EQ(value["uuid"])})
            is None
        )


@pytest.mark.parametrize("field", ["target_ipv4", "target_mac"])
def test_null_port_address_uses_automatic_allocation(backend, field):
    client, values = backend
    port = client.create(
        request("port", {"uuid": str(uuid.uuid4()), **values["port"], field: None})
    )
    assert getattr(port, field) is None
    assert port.ipv4 is not None and port.mac is not None


def test_address_uses_model_defaults(backend):
    client, values = backend
    value = {"uuid": str(uuid.uuid4()), **values["address"], "address": None}
    del value["kind"]
    address = client.create(request("address", value))
    assert address.kind == "reservation"
    assert address.address is not None


def test_nodeset_restores_actual_replica_with_observed_addresses(backend):
    from exordos_core.compute.node_set.dm import models as node_set_models

    client, values = backend
    node = client.create(
        request(
            "node",
            {
                "uuid": str(uuid.uuid4()),
                **values["node"],
                "default_network": {"subnet": values["port"]["subnet"]},
            },
        )
    )
    observed = request("set_agent_node", node.dump_to_simple_view())
    assert observed.value["default_network"]["port"]
    restored = node_set_models.Node.from_ua_resource(observed)
    assert restored.uuid == node.uuid
    assert restored.default_network["ipv4"] == observed.value["default_network"]["ipv4"]
