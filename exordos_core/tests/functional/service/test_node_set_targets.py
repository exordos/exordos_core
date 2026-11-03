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

from gcl_sdk.agents.universal.dm import models as ua
from gcl_sdk.infra.dm import models as infra
from gcl_sdk.paas.dm import services
import pytest
from restalchemy.dm import filters

from exordos_core.common import constants
from exordos_core.common.dm import targets
from exordos_core.compute.dm import models as compute
from exordos_core.config import service as config_service
from exordos_core.config.dm import models as config
from exordos_core.elements.builders import service as service_builder
from exordos_core.elements.dm import res_models


@pytest.fixture(params=["config", "service"])
def node_set_resource(request, user_api):
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
    if request.param == "config":
        instance = config.Config(
            name="target-config",
            project_id=constants.ZERO_UUID,
            path="/etc/target.txt",
            target=target,
            body=config.TextBodyConfig(content="target contents"),
        )
        builder = config_service.ConfigServiceBuilder()
        kind = "render"
    else:
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


@pytest.mark.parametrize("node_set_resource", ["service"], indirect=True)
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


@pytest.mark.parametrize("node_set_resource", ["config"], indirect=True)
def test_new_config_waits_for_empty_node_set(node_set_resource):
    instance, builder, first, add_node, derivatives, acknowledge = node_set_resource
    node_set_uuid = first.node_set
    first.node_set = None
    first.update()
    builder._iteration()
    waiting = config.Config(
        name="waiting-config",
        project_id=constants.ZERO_UUID,
        path="/etc/waiting.txt",
        target=targets.NodeSetTarget(node_set=node_set_uuid),
        body=config.TextBodyConfig(content="waiting"),
    )
    waiting.insert()
    builder._iteration()
    assert config.Config.objects.get_one(filters={"uuid": filters.EQ(waiting.uuid)})
    second = add_node()
    builder._iteration()
    children = ua.TargetResource.objects.get_all(
        filters={
            "master": filters.EQ(waiting.uuid),
            "kind": filters.EQ("render"),
        }
    )
    assert {r.agent for r in children} == {second.uuid}
