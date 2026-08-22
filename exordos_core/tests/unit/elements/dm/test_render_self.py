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
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""`$self:<field>` in a manifest resolves to the owning element."""

import uuid as sys_uuid

import pytest

from exordos_core.common import exceptions
from exordos_core.elements.dm.models import Element
from exordos_core.elements.dm.models import Resource

ELEMENT_UUID = sys_uuid.UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def resource():
    element = Element(
        uuid=ELEMENT_UUID,
        name="dbaas",
        version="0.2.2",
        status="ACTIVE",
        link="$dbaas",
    )
    return Resource(
        element=element,
        name="var_profile",
        resource_link_prefix="$core.vs.variables",
        value={},
    )


def test_render_self_uuid(resource):
    resource.value = {"setter": {"element": "$self:uuid"}}

    state = resource.render_target_state(engine=None)

    assert state["setter"]["element"] == str(ELEMENT_UUID)


@pytest.mark.parametrize(
    "field, expected",
    [
        ("name", "dbaas"),
        ("version", "0.2.2"),
        ("status", "ACTIVE"),
    ],
)
def test_render_self_field(resource, field, expected):
    resource.value = {"description": f"$self:{field}"}

    state = resource.render_target_state(engine=None)

    assert state["description"] == expected


def test_render_self_inside_fstring(resource):
    resource.value = {"description": 'f"element {$self:name} ({$self:uuid})"'}

    state = resource.render_target_state(engine=None)

    assert state["description"] == f"element dbaas ({ELEMENT_UUID})"


def test_render_self_unsupported_field(resource):
    resource.value = {"setter": {"element": "$self:project_id"}}

    with pytest.raises(
        exceptions.ValidateException, match="unsupported field `project_id`"
    ):
        resource.render_target_state(engine=None)


def test_render_self_without_field(resource):
    resource.value = {"setter": {"element": "$self"}}

    with pytest.raises(exceptions.ValidateException, match="no field specified"):
        resource.render_target_state(engine=None)
