#    Copyright 2026 Genesis Corporation.
#
#    All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.

from unittest import mock
import uuid as sys_uuid

from gcl_sdk.agents.universal import utils as sdk_utils
from gcl_sdk.agents.universal.dm import models as sdk_models

from exordos_core.elements.dm import models


ELEMENT = models.Element(
    name="test-element",
    version="1.0.0",
    status="ACTIVE",
    link="$test-element",
)


def _engine(parent):
    engine = mock.Mock()
    engine.get_resource_by_link.return_value = parent
    engine.get_namespace.return_value.element.name = "core"
    return engine


def _route():
    route = models.Resource(
        uuid=sys_uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        element=ELEMENT,
        name="route",
        resource_link_prefix="$core.network.lb.$lb.vhosts.$vhost.routes",
        value={"condition": {"kind": "raw"}},
    )
    return route


def test_actualize_sets_master_from_nested_resource_parent():
    parent = models.Resource(
        uuid=sys_uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        element=ELEMENT,
        name="vhost",
        resource_link_prefix="$core.network.lb.$lb.vhosts",
        value={},
    )
    route = _route()
    target_collection = sdk_models.TargetResource.objects
    engine = _engine(parent)

    with (
        mock.patch(
            "exordos_core.elements.dm.models.element_engine", engine
        ),
        mock.patch.object(route, "_find_actual_resource", return_value=None),
        mock.patch.object(route, "update"),
        mock.patch.object(type(target_collection), "get_all", return_value=[]),
        mock.patch.object(sdk_models.TargetResource, "insert"),
    ):
        route.actualize()

    assert route.target_resource.master == parent.uuid
    engine.get_resource_by_link.assert_called_once_with(
        element=ELEMENT,
        link="$core.network.lb.$lb.vhosts.$vhost",
    )


def test_actualize_updates_master_when_same_resource_moves_to_new_parent():
    old_parent = models.Resource(
        uuid=sys_uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        element=ELEMENT,
        name="old-vhost",
        resource_link_prefix="$core.network.lb.$old_lb.vhosts",
        value={},
    )
    new_parent = models.Resource(
        uuid=sys_uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
        element=ELEMENT,
        name="new-vhost",
        resource_link_prefix="$core.network.lb.$new_lb.vhosts",
        value={},
    )
    route = _route()
    route.resource_link_prefix = (
        "$core.network.lb.$new_lb.vhosts.$new-vhost.routes"
    )
    engine = _engine(new_parent)
    target_state = route.render_target_state(engine=engine)
    target = sdk_models.TargetResource(
        uuid=route.uuid,
        kind="em_core_network_lb_vhosts_routes",
        res_uuid=sdk_models.TargetResource.gen_res_uuid(
            route.uuid, "em_core_network_lb_vhosts_routes"
        ),
        value=target_state,
        hash=sdk_utils.calculate_hash(target_state),
        full_hash=sdk_utils.calculate_hash(target_state),
        tracked_at=route.updated_at,
        master=old_parent.uuid,
    )
    route.target_resource = target

    with (
        mock.patch(
            "exordos_core.elements.dm.models.element_engine", engine
        ),
        mock.patch.object(route, "_find_actual_resource", return_value=None),
        mock.patch.object(route, "update"),
        mock.patch.object(target, "update") as target_update,
    ):
        route.actualize()

    assert route.uuid == sys_uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    assert target.master == new_parent.uuid
    engine.get_resource_by_link.assert_called_once_with(
        element=ELEMENT,
        link="$core.network.lb.$new_lb.vhosts.$new-vhost",
    )
    target_update.assert_called_once()
