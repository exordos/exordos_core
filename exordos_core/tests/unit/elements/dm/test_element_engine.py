#    Copyright 2025 Genesis Corporation.
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

import uuid as sys_uuid

import pytest

from exordos_core.elements.dm.models import Element
from exordos_core.elements.dm.models import Manifest
from exordos_core.elements.dm.models import element_engine


class TestManifestCheckNoDependents:
    """Tests for Manifest._check_no_dependents() method."""

    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        """Fixture to ensure element_engine is cleaned up after each test."""
        yield
        # Teardown: cleanup any elements added during the test
        for element in element_engine.get_elements():
            element_engine.remove_element(element)

    def test_check_passes_when_no_dependents(self, setup_teardown):
        """Test that check passes when no other element requires the given element."""
        # Create dbaas element (the dependency)
        dbaas_element = Element(
            uuid=sys_uuid.UUID("11111111-1111-1111-1111-111111111111"),
            name="dbaas",
            version="0.2.2",
            link="$dbaas",
        )
        dbaas_element.requirements = {}

        # Create stand element with no dependency on dbaas
        stand_element = Element(
            uuid=sys_uuid.UUID("33333333-3333-3333-3333-333333333333"),
            name="stand",
            version="1.0.0",
            link="$stand",
        )
        stand_element.requirements = {
            "core": {"from_version": "0.0.0", "to_version": "1.0.0"}
        }

        # Setup element_engine
        element_engine.add_element(dbaas_element)
        element_engine.add_element(stand_element)

        # Check should pass (no exception)
        Manifest._check_no_dependents(dbaas_element)

    def test_check_detects_dependency_with_version_range(self, setup_teardown):
        """Test that check detects dependency when element version is in required range."""
        # Create dbaas element (the dependency)
        dbaas_element = Element(
            uuid=sys_uuid.UUID("11111111-1111-1111-1111-111111111111"),
            name="dbaas",
            version="0.2.2",
            link="$dbaas",
        )
        dbaas_element.requirements = {}

        # Create stand element that requires dbaas with version range
        stand_element = Element(
            uuid=sys_uuid.UUID("33333333-3333-3333-3333-333333333333"),
            name="stand",
            version="1.0.0",
            link="$stand",
        )
        stand_element.requirements = {
            "dbaas": {"from_version": "0.0.0", "to_version": "1.0.0"}
        }

        # Setup element_engine
        element_engine.add_element(dbaas_element)
        element_engine.add_element(stand_element)

        # Check should fail (0.2.2 is within range 0.0.0-1.0.0)
        with pytest.raises(ValueError) as exc_info:
            Manifest._check_no_dependents(dbaas_element)

        assert "Cannot uninstall element 'dbaas' version '0.2.2'" in str(exc_info.value)
        assert "because it is required by element 'stand' version '1.0.0'" in str(
            exc_info.value
        )

    def test_check_allows_version_below_range(self, setup_teardown):
        """Test that check allows when element version is below required range."""
        # Create dbaas element version 0.2.2
        dbaas_element = Element(
            uuid=sys_uuid.UUID("11111111-1111-1111-1111-111111111111"),
            name="dbaas",
            version="0.2.2",
            link="$dbaas",
        )
        dbaas_element.requirements = {}

        # Create stand element that requires version range starting at 0.3.0
        stand_element = Element(
            uuid=sys_uuid.UUID("33333333-3333-3333-3333-333333333333"),
            name="stand",
            version="1.0.0",
            link="$stand",
        )
        stand_element.requirements = {
            "dbaas": {"from_version": "0.3.0", "to_version": "1.0.0"}
        }

        # Setup element_engine
        element_engine.add_element(dbaas_element)
        element_engine.add_element(stand_element)

        # Check should pass (0.2.2 < 0.3.0)
        Manifest._check_no_dependents(dbaas_element)

    def test_check_allows_version_above_range(self, setup_teardown):
        """Test that check allows when element version is above required range."""
        # Create dbaas element version 0.2.2
        dbaas_element = Element(
            uuid=sys_uuid.UUID("11111111-1111-1111-1111-111111111111"),
            name="dbaas",
            version="0.2.2",
            link="$dbaas",
        )
        dbaas_element.requirements = {}

        # Create stand element that requires version range ending at 0.2.0
        stand_element = Element(
            uuid=sys_uuid.UUID("33333333-3333-3333-3333-333333333333"),
            name="stand",
            version="1.0.0",
            link="$stand",
        )
        stand_element.requirements = {
            "dbaas": {"from_version": "0.0.0", "to_version": "0.2.0"}
        }

        # Setup element_engine
        element_engine.add_element(dbaas_element)
        element_engine.add_element(stand_element)

        # Check should pass (0.2.2 > 0.2.0)
        Manifest._check_no_dependents(dbaas_element)

    def test_check_fails_with_only_from_version_when_in_range(self, setup_teardown):
        """Test that check fails when version is within from_version range."""
        # Create dbaas element version 0.5.0
        dbaas_element = Element(
            uuid=sys_uuid.UUID("11111111-1111-1111-1111-111111111111"),
            name="dbaas",
            version="0.5.0",
            link="$dbaas",
        )
        dbaas_element.requirements = {}

        # Create stand element that requires version >= 0.3.0 (no to_version)
        stand_element = Element(
            uuid=sys_uuid.UUID("33333333-3333-3333-3333-333333333333"),
            name="stand",
            version="1.0.0",
            link="$stand",
        )
        stand_element.requirements = {"dbaas": {"from_version": "0.3.0"}}

        # Setup element_engine
        element_engine.add_element(dbaas_element)
        element_engine.add_element(stand_element)

        # Check should fail (0.5.0 >= 0.3.0, so it's in range)
        with pytest.raises(ValueError) as exc_info:
            Manifest._check_no_dependents(dbaas_element)

        assert "Cannot uninstall element 'dbaas' version '0.5.0'" in str(exc_info.value)

    def test_check_fails_with_only_to_version_when_in_range(self, setup_teardown):
        """Test that check fails when version is within to_version range."""
        # Create dbaas element version 0.1.5
        dbaas_element = Element(
            uuid=sys_uuid.UUID("11111111-1111-1111-1111-111111111111"),
            name="dbaas",
            version="0.1.5",
            link="$dbaas",
        )
        dbaas_element.requirements = {}

        # Create stand element that requires version <= 0.2.0 (no from_version)
        stand_element = Element(
            uuid=sys_uuid.UUID("33333333-3333-3333-3333-333333333333"),
            name="stand",
            version="1.0.0",
            link="$stand",
        )
        stand_element.requirements = {"dbaas": {"to_version": "0.2.0"}}

        # Setup element_engine
        element_engine.add_element(dbaas_element)
        element_engine.add_element(stand_element)

        # Check should fail (0.1.5 <= 0.2.0, so it's in range)
        with pytest.raises(ValueError) as exc_info:
            Manifest._check_no_dependents(dbaas_element)

        assert "Cannot uninstall element 'dbaas' version '0.1.5'" in str(exc_info.value)

    def test_check_passes_with_only_from_version_when_below_range(self, setup_teardown):
        """Test that check passes when version is below from_version."""
        # Create dbaas element version 0.2.0
        dbaas_element = Element(
            uuid=sys_uuid.UUID("11111111-1111-1111-1111-111111111111"),
            name="dbaas",
            version="0.2.0",
            link="$dbaas",
        )
        dbaas_element.requirements = {}

        # Create stand element that requires version >= 0.3.0
        stand_element = Element(
            uuid=sys_uuid.UUID("33333333-3333-3333-3333-333333333333"),
            name="stand",
            version="1.0.0",
            link="$stand",
        )
        stand_element.requirements = {"dbaas": {"from_version": "0.3.0"}}

        # Setup element_engine
        element_engine.add_element(dbaas_element)
        element_engine.add_element(stand_element)

        # Check should pass (0.2.0 < 0.3.0, so it's below range)
        Manifest._check_no_dependents(dbaas_element)

    def test_check_skips_same_element(self, setup_teardown):
        """Test that check skips the element itself (same UUID)."""
        # Create dbaas element
        dbaas_element = Element(
            uuid=sys_uuid.UUID("11111111-1111-1111-1111-111111111111"),
            name="dbaas",
            version="0.2.2",
            link="$dbaas",
        )
        dbaas_element.requirements = {}

        # Setup element_engine with only dbaas
        element_engine.add_element(dbaas_element)

        # Check should pass (only element is itself)
        Manifest._check_no_dependents(dbaas_element)
