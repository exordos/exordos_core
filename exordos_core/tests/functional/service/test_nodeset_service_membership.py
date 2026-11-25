# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import uuid

from gcl_sdk.agents.universal.dm import models as ua
from gcl_sdk.infra.dm import models as infra
from gcl_sdk.paas.dm import services
import pytest
from restalchemy.dm import filters

from exordos_core.common import constants
from exordos_core.common.dm import targets
from exordos_core.compute.dm import models as compute
from exordos_core.elements.builders import service as service_builder
from exordos_core.elements.dm import res_models


@pytest.fixture
def node_set_resource(user_api):
    disk = infra.RootDiskSpec(size=1, image="https://example.com/base.raw.zst")
    node_set = compute.NodeSet(
        name="target-set",
        project_id=constants.ZERO_UUID,
        cores=1,
        ram=1024,
        replicas=1,
        disk_spec=infra.SetRootDiskSpec(
            size=1, image="https://example.com/base.raw.zst"
        ),
    )
    node_set.insert()

    def add_node(register=True):
        node = compute.Node(
            name="target-node",
            project_id=constants.ZERO_UUID,
            cores=1,
            ram=1024,
            disk_spec=disk,
            node_set=node_set.uuid,
        )
        node.insert()
        if register:
            ua.UniversalAgent(
                uuid=node.uuid, node=node.uuid, name="target-agent"
            ).insert()
        return node

    first = add_node()
    target = targets.NodeSetTarget(node_set=node_set.uuid)
    instance = res_models.Service(
        name="target-service",
        project_id=constants.ZERO_UUID,
        path="/usr/bin/sleep infinity",
        target=target,
        service_type=services.ServiceTypeSimple(),
    )
    builder = service_builder.ServiceNodeBuilder()
    kind = "service_agent_node"
    instance.insert()

    def derivatives():
        return ua.TargetResource.objects.get_all(
            filters={
                "master": filters.EQ(instance.uuid),
                "kind": filters.EQ(kind),
            }
        )

    def acknowledge():
        for resource in derivatives():
            actual = ua.Resource.objects.get_one_or_none(
                filters={
                    "uuid": filters.EQ(resource.uuid),
                    "kind": filters.EQ(kind),
                }
            )
            if actual is None:
                actual = ua.Resource(
                    uuid=resource.uuid,
                    kind=kind,
                    res_uuid=resource.res_uuid,
                    node=resource.agent,
                    value={**resource.value, "status": "ACTIVE"},
                    status="ACTIVE",
                    hash=resource.hash,
                    full_hash="ack-" + resource.hash,
                )
                actual.insert()
        builder._iteration()

    builder._iteration()
    acknowledge()
    return instance, builder, first, add_node, derivatives, acknowledge


def test_node_set_growth_updates_existing_targets(node_set_resource):
    instance, builder, first, add_node, derivatives, acknowledge = node_set_resource
    original = derivatives()[0]
    second = add_node()

    builder._iteration()

    children = derivatives()
    assert {r.agent for r in children} == {first.uuid, second.uuid}
    assert (
        next(r for r in children if r.agent == first.uuid).created_at
        == original.created_at
    )
    assert (
        type(instance)
        .objects.get_one(filters={"uuid": filters.EQ(instance.uuid)})
        .status
        == "IN_PROGRESS"
    )

    acknowledge()
    assert (
        type(instance)
        .objects.get_one(filters={"uuid": filters.EQ(instance.uuid)})
        .status
        == "ACTIVE"
    )
    unchanged = {r.uuid: r.updated_at for r in derivatives()}
    builder._iteration()
    assert {r.uuid: r.updated_at for r in derivatives()} == unchanged


def test_node_set_shrink_removes_only_departed_targets(node_set_resource):
    instance, builder, first, add_node, derivatives, acknowledge = node_set_resource
    second = add_node()
    builder._iteration()
    acknowledge()
    assert {r.agent for r in derivatives()} == {first.uuid, second.uuid}
    original = next(r for r in derivatives() if r.agent == first.uuid)
    second.node_set = None
    second.update()

    builder._iteration()

    children = derivatives()
    assert {r.agent for r in children} == {first.uuid}
    assert children[0].uuid == original.uuid
    assert children[0].created_at == original.created_at


def test_node_set_target_retries_after_agent_registration(node_set_resource):
    instance, builder, first, add_node, derivatives, acknowledge = node_set_resource
    original = derivatives()[0]
    second = add_node(register=False)
    builder._iteration()
    assert {r.agent for r in derivatives()} == {first.uuid}
    assert derivatives()[0].created_at == original.created_at

    ua.UniversalAgent(uuid=second.uuid, node=second.uuid, name="late-agent").insert()
    builder._iteration()
    assert {r.agent for r in derivatives()} == {first.uuid, second.uuid}
    acknowledge()
    assert (
        type(instance)
        .objects.get_one(filters={"uuid": filters.EQ(instance.uuid)})
        .status
        == "ACTIVE"
    )


def test_membership_scan_advances_past_pending_services(node_set_resource, monkeypatch):
    instance, builder, _, add_node, _, _ = node_set_resource
    other = res_models.Service(
        uuid=uuid.UUID(int=(1 << 128) - 1),
        name="another-service",
        project_id=instance.project_id,
        path="/usr/bin/sleep 86400",
        target=instance.target,
        service_type=services.ServiceTypeSimple(),
    )
    other.insert()
    builder._iteration()
    add_node(register=False)
    monkeypatch.setattr(constants, "DEFAULT_SQL_LIMIT", 1)
    monkeypatch.setattr(
        service_builder.CommonBuilder, "_get_updated_instances", lambda self: ()
    )

    assert [service.uuid for service in builder._get_updated_instances()] == [
        instance.uuid
    ]
    assert [service.uuid for service in builder._get_updated_instances()] == [
        other.uuid
    ]


def test_node_set_target_survives_zero_members(node_set_resource):
    instance, builder, first, add_node, derivatives, acknowledge = node_set_resource
    first.node_set = None
    first.update()
    builder._iteration()
    assert derivatives() == []
    assert type(instance).objects.get_one(filters={"uuid": filters.EQ(instance.uuid)})

    second = add_node()
    builder._iteration()
    assert {r.agent for r in derivatives()} == {second.uuid}
    acknowledge()


def test_monopoly_service_keeps_one_existing_member(node_set_resource):
    instance, builder, first, add_node, derivatives, acknowledge = node_set_resource
    instance = type(instance).objects.get_one(
        filters={"uuid": filters.EQ(instance.uuid)}
    )
    instance.service_type = services.ServiceTypeMonopoly()
    instance.update()
    builder._iteration()
    acknowledge()
    original = derivatives()[0]

    second = add_node()
    builder._iteration()
    assert len(derivatives()) == 1
    assert derivatives()[0].agent == first.uuid
    assert derivatives()[0].created_at == original.created_at

    first.node_set = None
    first.update()
    builder._iteration()
    assert {r.agent for r in derivatives()} == {second.uuid}
