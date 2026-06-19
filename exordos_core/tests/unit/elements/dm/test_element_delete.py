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

from unittest.mock import MagicMock
from unittest.mock import patch
import uuid as sys_uuid

from gcl_sdk.agents.universal.dm import models as sdk_models
from restalchemy.storage.sql import orm

from exordos_core.elements.dm.models import Element
from exordos_core.elements.dm.models import Resource

ELEMENT_UUID = sys_uuid.UUID("11111111-1111-1111-1111-111111111111")
AGENT_UUID = sys_uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
AGENT_UUID_2 = sys_uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _collection_returning(values):
    """Return a mock _ObjectCollection class whose instances return *values* from get_all."""
    collection = MagicMock()
    collection.get_all.return_value = values
    return MagicMock(return_value=collection)


def _make_element():
    element = Element(
        uuid=ELEMENT_UUID,
        name="test-element",
        version="1.0.0",
        link="$test-element",
    )
    element.requirements = {}
    return element


def _make_resource_mock(agent_uuid=None):
    """Return a mock Resource whose target_resource.agent is agent_uuid."""
    resource = MagicMock(spec=Resource)
    if agent_uuid is not None:
        target_resource = MagicMock()
        target_resource.agent = agent_uuid
        resource.target_resource = target_resource
    else:
        resource.target_resource = None
    return resource


class TestElementDeleteAgentGC:
    """Tests for the agent garbage-collection logic in Element.delete()."""

    def test_deletes_agent_when_no_remaining_resources(self):
        """After element deletion the agent must be removed if it has no
        other target_resources left."""
        element = _make_element()
        resource = _make_resource_mock(agent_uuid=AGENT_UUID)
        agent = MagicMock()

        with (
            patch.object(orm.SQLStorableMixin, "delete"),
            patch.object(
                Resource, "_ObjectCollection", _collection_returning([resource])
            ),
            patch.object(
                sdk_models.TargetResource,
                "_ObjectCollection",
                _collection_returning([]),  # no remaining target_resources
            ),
            patch.object(
                sdk_models.UniversalAgent,
                "_ObjectCollection",
                _collection_returning([agent]),
            ),
        ):
            element.delete()

        agent.delete.assert_called_once_with(session=None)

    def test_keeps_agent_when_other_resources_remain(self):
        """Agent must NOT be deleted when other elements still use it."""
        element = _make_element()
        resource = _make_resource_mock(agent_uuid=AGENT_UUID)
        other_target = MagicMock()
        agent = MagicMock()

        with (
            patch.object(orm.SQLStorableMixin, "delete"),
            patch.object(
                Resource, "_ObjectCollection", _collection_returning([resource])
            ),
            patch.object(
                sdk_models.TargetResource,
                "_ObjectCollection",
                _collection_returning([other_target]),  # agent still has resources
            ),
            patch.object(
                sdk_models.UniversalAgent,
                "_ObjectCollection",
                _collection_returning([agent]),
            ),
        ):
            element.delete()

        agent.delete.assert_not_called()

    def test_no_crash_when_resource_has_no_target_resource(self):
        """Element with resources that were never actualized (target_resource=None)
        must delete cleanly without errors."""
        element = _make_element()
        resource = _make_resource_mock(agent_uuid=None)

        with (
            patch.object(orm.SQLStorableMixin, "delete"),
            patch.object(
                Resource, "_ObjectCollection", _collection_returning([resource])
            ),
            patch.object(
                sdk_models.TargetResource,
                "_ObjectCollection",
                _collection_returning([]),
            ),
            patch.object(
                sdk_models.UniversalAgent,
                "_ObjectCollection",
                _collection_returning([]),
            ),
        ):
            element.delete()  # must not raise

    def test_no_crash_when_element_has_no_resources(self):
        """Element with no resources (e.g. never actualized) must delete cleanly."""
        element = _make_element()

        with (
            patch.object(orm.SQLStorableMixin, "delete"),
            patch.object(Resource, "_ObjectCollection", _collection_returning([])),
            patch.object(
                sdk_models.TargetResource,
                "_ObjectCollection",
                _collection_returning([]),
            ),
            patch.object(
                sdk_models.UniversalAgent,
                "_ObjectCollection",
                _collection_returning([]),
            ),
        ):
            element.delete()  # must not raise

    def test_gcs_only_agent_with_no_remaining_resources(self):
        """When two resources use different agents and only one becomes orphaned
        after deletion, only that agent must be removed."""
        element = _make_element()
        resource_a = _make_resource_mock(agent_uuid=AGENT_UUID)
        resource_b = _make_resource_mock(agent_uuid=AGENT_UUID_2)
        agent_b = MagicMock()
        orphan_target = MagicMock()

        # Build a TargetResource collection whose get_all depends on the agent filter.
        def _remaining(filters=None, **_kw):
            agent_val = str(filters["agent"].value)
            # AGENT_UUID still has other resources; AGENT_UUID_2 has none.
            return [orphan_target] if agent_val == str(AGENT_UUID) else []

        tr_collection = MagicMock()
        tr_collection.get_all.side_effect = _remaining

        with (
            patch.object(orm.SQLStorableMixin, "delete"),
            patch.object(
                Resource,
                "_ObjectCollection",
                _collection_returning([resource_a, resource_b]),
            ),
            patch.object(
                sdk_models.TargetResource,
                "_ObjectCollection",
                MagicMock(return_value=tr_collection),
            ),
            patch.object(
                sdk_models.UniversalAgent,
                "_ObjectCollection",
                _collection_returning([agent_b]),
            ),
        ):
            element.delete()

        # agent_b (AGENT_UUID_2) has no remaining resources → must be deleted.
        agent_b.delete.assert_called_once_with(session=None)
