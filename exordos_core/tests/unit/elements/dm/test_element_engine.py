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

from exordos_core.common import exceptions
from exordos_core.elements.dm.models import Element
from exordos_core.elements.dm.models import ElementEngine
from exordos_core.elements.dm.models import Export
from exordos_core.elements.dm.models import Manifest
from exordos_core.elements.dm.models import Resource
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
        with pytest.raises(exceptions.ValidateException) as exc_info:
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
        with pytest.raises(exceptions.ValidateException) as exc_info:
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
        with pytest.raises(exceptions.ValidateException) as exc_info:
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


class TestElementEngineGetResourceByExportLink:
    """Tests for get_resource_by_export_link method."""

    def setup_method(self) -> None:
        self.engine = ElementEngine()
        self.engine._namespaces = {}
        self.engine._resource_exports = {}

    def test_direct_lookup_succeeds(self):
        """Test that direct lookup of export link works."""

        element = Element(
            uuid=sys_uuid.UUID("11111111-1111-1111-1111-111111111111"),
            name="dbaas",
            version="0.2.2",
            link="$dbaas",
        )
        resource = Resource(
            uuid=sys_uuid.UUID("22222222-2222-2222-2222-222222222222"),
            name="cluster_pg",
            element=element,
            resource_link_prefix="$dbaas.types.postgres.instances",
            value={"name": "test"},
        )
        export = Export(
            uuid=sys_uuid.UUID("33333333-3333-3333-3333-333333333333"),
            name="cluster",
            element=element,
            link="$dbaas.types.postgres.instances.$cluster_pg",
        )
        self.engine._namespaces["$dbaas"] = type(
            "Namespace", (), {"element": element}
        )()
        self.engine._resource_exports[export.full_link] = resource
        manifest = Manifest(
            uuid=sys_uuid.UUID("44444444-4444-4444-4444-444444444444"),
            name="communal_pg_cluster",
            version="0.2.2",
        )

        result = self.engine.get_resource_by_export_link(
            manifest=manifest,
            from_element=element,
            link="$dbaas.types.postgres.instances.$cluster_pg",
        )

        assert result is resource
        assert result.name == "cluster_pg"

    def test_import_with_different_element_prefix_resolves_by_path_structure(self):
        """Test that import link resolves correctly when element prefix matches.

        Import link: $dbaas.types.postgres.instances.$cluster_pg
        Export link: $dbaas.types.postgres.instances.$cluster_pg

        The import and export have the same element prefix and path structure,
        so the import should resolve to the export.
        """

        # Export element (dbaas)
        export_element = Element(
            uuid=sys_uuid.UUID("11111111-1111-1111-1111-111111111111"),
            name="dbaas",
            version="0.2.2",
            link="$dbaas",
        )
        resource = Resource(
            uuid=sys_uuid.UUID("22222222-2222-2222-2222-222222222222"),
            name="cluster_pg",
            element=export_element,
            resource_link_prefix="$dbaas.types.postgres.instances",
            value={"name": "test"},
        )
        export = Export(
            uuid=sys_uuid.UUID("33333333-3333-3333-3333-333333333333"),
            name="cluster",
            element=export_element,
            link="$dbaas.types.postgres.instances.$cluster_pg",
        )

        # Import element (dbaas) - same as export element
        import_element = Element(
            uuid=sys_uuid.UUID("55555555-5555-5555-5555-555555555555"),
            name="dbaas",
            version="0.2.2",
            link="$dbaas",
        )

        manifest = Manifest(
            uuid=sys_uuid.UUID("44444444-4444-4444-4444-444444444444"),
            name="stand",
            version="1.0.0",
        )

        # Setup engine with export
        self.engine._namespaces["$dbaas"] = type(
            "Namespace", (), {"element": export_element}
        )()
        self.engine._resource_exports[export.full_link] = resource

        # Import link has matching element prefix ($dbaas)
        result = self.engine.get_resource_by_export_link(
            manifest=manifest,
            from_element=import_element,
            link="$dbaas.types.postgres.instances.$cluster_pg",
        )

        assert result is resource
        assert result.name == "cluster_pg"
        assert result.element.name == "dbaas"

    def test_import_with_different_element_prefix_and_different_resource_name(self):
        """Test import resolution with different resource name in path."""

        export_element = Element(
            uuid=sys_uuid.UUID("11111111-1111-1111-1111-111111111111"),
            name="dbaas",
            version="0.2.2",
            link="$dbaas",
        )
        resource = Resource(
            uuid=sys_uuid.UUID("22222222-2222-2222-2222-222222222222"),
            name="pg18",
            element=export_element,
            resource_link_prefix="$dbaas.types.postgres.versions",
            value={"version": "18.0"},
        )
        export = Export(
            uuid=sys_uuid.UUID("33333333-3333-3333-3333-333333333333"),
            name="pg18_export",
            element=export_element,
            link="$dbaas.types.postgres.versions.$pg18",
        )

        import_element = Element(
            uuid=sys_uuid.UUID("55555555-5555-5555-5555-555555555555"),
            name="dbaas",
            version="0.0.1",
            link="$dbaas",
        )

        manifest = Manifest(
            uuid=sys_uuid.UUID("44444444-4444-4444-4444-444444444444"),
            name="stand",
            version="1.0.0",
        )

        self.engine._namespaces["$dbaas"] = type(
            "Namespace", (), {"element": export_element}
        )()
        self.engine._resource_exports[export.full_link] = resource

        result = self.engine.get_resource_by_export_link(
            manifest=manifest,
            from_element=import_element,
            link="$dbaas.types.postgres.versions.$pg18",
        )

        assert result is resource
        assert result.name == "pg18"

    def test_import_with_mismatched_path_structure_fails(self):
        """Test that import with mismatched path structure raises error."""

        export_element = Element(
            uuid=sys_uuid.UUID("11111111-1111-1111-1111-111111111111"),
            name="dbaas",
            version="0.2.2",
            link="$dbaas",
        )
        resource = Resource(
            uuid=sys_uuid.UUID("22222222-2222-2222-2222-222222222222"),
            name="cluster_pg",
            element=export_element,
            resource_link_prefix="$dbaas.types.postgres.instances",
            value={"name": "test"},
        )
        export = Export(
            uuid=sys_uuid.UUID("33333333-3333-3333-3333-333333333333"),
            name="cluster",
            element=export_element,
            link="$dbaas.types.postgres.instances.$cluster_pg",
        )

        import_element = Element(
            uuid=sys_uuid.UUID("55555555-5555-5555-5555-555555555555"),
            name="stand",
            version="1.0.0",
            link="$stand",
        )

        manifest = Manifest(
            uuid=sys_uuid.UUID("44444444-4444-4444-4444-444444444444"),
            name="stand",
            version="1.0.0",
        )

        self.engine._namespaces["$dbaas"] = type(
            "Namespace", (), {"element": export_element}
        )()
        self.engine._resource_exports[export.full_link] = resource

        # path structure doesn't match (instances vs versions)
        with pytest.raises(exceptions.ValidateException) as exc_info:
            self.engine.get_resource_by_export_link(
                manifest=manifest,
                from_element=import_element,
                link="$stand.types.postgres.versions.$cluster_pg",
            )

        assert "is not in export list" in str(exc_info.value)

    def test_multiple_exports_with_same_resource_name_returns_correct_one(self):
        """Test that when multiple exports have same resource name, correct one is returned."""

        # First export
        export_element1 = Element(
            uuid=sys_uuid.UUID("11111111-1111-1111-1111-111111111111"),
            name="dbaas",
            version="0.2.2",
            link="$dbaas",
        )
        resource1 = Resource(
            uuid=sys_uuid.UUID("22222222-2222-2222-2222-222222222222"),
            name="cluster_pg",
            element=export_element1,
            resource_link_prefix="$dbaas.types.postgres.instances",
            value={"name": "test1"},
        )
        export1 = Export(
            uuid=sys_uuid.UUID("33333333-3333-3333-3333-333333333333"),
            name="cluster",
            element=export_element1,
            link="$dbaas.types.postgres.instances.$cluster_pg",
        )

        # Second export with same resource name but different path
        export_element2 = Element(
            uuid=sys_uuid.UUID("66666666-6666-6666-6666-666666666666"),
            name="dbaas",
            version="1.0.0",
            link="$dbaas",
        )
        resource2 = Resource(
            uuid=sys_uuid.UUID("77777777-7777-7777-7777-777777777777"),
            name="cluster_pg",
            element=export_element2,
            resource_link_prefix="$dbaas.types.volumes.instances",
            value={"name": "test2"},
        )
        export2 = Export(
            uuid=sys_uuid.UUID("88888888-8888-8888-8888-888888888888"),
            name="volume",
            element=export_element2,
            link="$dbaas.types.volumes.instances.$cluster_pg",
        )

        import_element = Element(
            uuid=sys_uuid.UUID("55555555-5555-5555-5555-555555555555"),
            name="dbaas",
            version="1.0.0",
            link="$dbaas",
        )

        manifest = Manifest(
            uuid=sys_uuid.UUID("44444444-4444-4444-4444-444444444444"),
            name="stand",
            version="1.0.0",
        )

        self.engine._namespaces["$dbaas"] = type(
            "Namespace", (), {"element": export_element1}
        )()
        self.engine._resource_exports[export1.full_link] = resource1
        self.engine._resource_exports[export2.full_link] = resource2

        # import path matches the postgres export
        result = self.engine.get_resource_by_export_link(
            manifest=manifest,
            from_element=import_element,
            link="$dbaas.types.postgres.instances.$cluster_pg",
        )

        assert result is resource1
        assert result.element.name == "dbaas"
        assert result.value["name"] == "test1"

    def test_no_matching_export_raises_error(self):
        """Test that when no matching export exists, exceptions.ValidateException is raised."""

        export_element = Element(
            uuid=sys_uuid.UUID("11111111-1111-1111-1111-111111111111"),
            name="dbaas",
            version="0.2.2",
            link="$dbaas",
        )
        resource = Resource(
            uuid=sys_uuid.UUID("22222222-2222-2222-2222-222222222222"),
            name="cluster_pg",
            element=export_element,
            resource_link_prefix="$dbaas.types.postgres.instances",
            value={"name": "test"},
        )
        export = Export(
            uuid=sys_uuid.UUID("33333333-3333-3333-3333-333333333333"),
            name="cluster",
            element=export_element,
            link="$dbaas.types.postgres.instances.$cluster_pg",
        )

        import_element = Element(
            uuid=sys_uuid.UUID("55555555-5555-5555-5555-555555555555"),
            name="stand",
            version="1.0.0",
            link="$stand",
        )

        manifest = Manifest(
            uuid=sys_uuid.UUID("44444444-4444-4444-4444-444444444444"),
            name="stand",
            version="1.0.0",
        )

        self.engine._namespaces["$dbaas"] = type(
            "Namespace", (), {"element": export_element}
        )()
        self.engine._resource_exports[export.full_link] = resource

        # resource name doesn't match any export
        with pytest.raises(exceptions.ValidateException) as exc_info:
            self.engine.get_resource_by_export_link(
                manifest=manifest,
                from_element=import_element,
                link="$stand.types.postgres.instances.$nonexistent",
            )

        assert "is not in export list" in str(exc_info.value)

    def test_multiple_exports_with_same_path_structure_selects_correct_element(self):
        """Test that when multiple elements export same path structure, correct one is selected.

        This is the bug case from GitHub PR #346:
        - Element foo exports $foo.types.postgres.versions.$cluster
        - Element bar exports $bar.types.postgres.versions.$cluster
        - Element baz imports from bar with link $bar.types.postgres.versions.$cluster

        The import should resolve to bar's export, not foo's export,
        even though both have the same path structure.
        """

        # First export from element 'foo'
        foo_element = Element(
            uuid=sys_uuid.UUID("11111111-1111-1111-1111-111111111111"),
            name="foo",
            version="0.2.2",
            link="$foo",
        )
        resource_foo = Resource(
            uuid=sys_uuid.UUID("22222222-2222-2222-2222-222222222222"),
            name="cluster",
            element=foo_element,
            resource_link_prefix="$foo.types.postgres.versions",
            value={"source": "foo"},
        )
        export_foo = Export(
            uuid=sys_uuid.UUID("33333333-3333-3333-3333-333333333333"),
            name="cluster",
            element=foo_element,
            link="$foo.types.postgres.versions.$cluster",
        )

        # Second export from element 'bar' with same path structure
        bar_element = Element(
            uuid=sys_uuid.UUID("44444444-4444-4444-4444-444444444444"),
            name="bar",
            version="0.2.2",
            link="$bar",
        )
        resource_bar = Resource(
            uuid=sys_uuid.UUID("55555555-5555-5555-5555-555555555555"),
            name="cluster",
            element=bar_element,
            resource_link_prefix="$bar.types.postgres.versions",
            value={"source": "bar"},
        )
        export_bar = Export(
            uuid=sys_uuid.UUID("66666666-6666-6666-6666-666666666666"),
            name="cluster",
            element=bar_element,
            link="$bar.types.postgres.versions.$cluster",
        )

        # Import element 'baz' importing from 'bar'
        baz_element = Element(
            uuid=sys_uuid.UUID("77777777-7777-7777-7777-777777777777"),
            name="baz",
            version="1.0.0",
            link="$baz",
        )

        manifest = Manifest(
            uuid=sys_uuid.UUID("88888888-8888-8888-8888-888888888888"),
            name="test_manifest",
            version="1.0.0",
        )

        # Setup engine with both exports
        self.engine._namespaces["$foo"] = type(
            "Namespace", (), {"element": foo_element}
        )()
        self.engine._namespaces["$bar"] = type(
            "Namespace", (), {"element": bar_element}
        )()
        self.engine._resource_exports[export_foo.full_link] = resource_foo
        self.engine._resource_exports[export_bar.full_link] = resource_bar

        # Import from bar should resolve to bar's export, not foo's
        result = self.engine.get_resource_by_export_link(
            manifest=manifest,
            from_element=baz_element,
            link="$bar.types.postgres.versions.$cluster",
        )

        assert result is resource_bar
        assert result.element.name == "bar"
        assert result.value["source"] == "bar"
        # Should NOT be foo's resource
        assert result is not resource_foo

    def test_same_element_export_with_different_paths_returns_correct_resource(
        self,
    ):
        """Test that same element exports with different paths resolve correctly.

        Element exports two resources with same name in different paths:
        - $test_export_node_1.$core.compute.nodes.$test_node
        - $test_export_node_1.$core.storage.volumes.$test_node

        Import links to compute.nodes path, must return compute resource,
        not storage one.
        """

        export_element = Element(
            uuid=sys_uuid.UUID("11111111-1111-1111-1111-111111111111"),
            name="test_export_node_1",
            version="0.1.0",
            link="$test_export_node_1",
        )
        compute_resource = Resource(
            uuid=sys_uuid.UUID("22222222-2222-2222-2222-222222222222"),
            name="test_node",
            element=export_element,
            resource_link_prefix="$test_export_node_1.$core.compute.nodes",
            value={"type": "compute"},
        )
        compute_export = Export(
            uuid=sys_uuid.UUID("33333333-3333-3333-3333-333333333333"),
            name="compute_export",
            element=export_element,
            link="$test_export_node_1.$core.compute.nodes.$test_node",
        )
        storage_resource = Resource(
            uuid=sys_uuid.UUID("44444444-4444-4444-4444-444444444444"),
            name="test_node",
            element=export_element,
            resource_link_prefix="$test_export_node_1.$core.storage.volumes",
            value={"type": "storage"},
        )
        storage_export = Export(
            uuid=sys_uuid.UUID("55555555-5555-5555-5555-555555555555"),
            name="storage_export",
            element=export_element,
            link="$test_export_node_1.$core.storage.volumes.$test_node",
        )

        import_element = Element(
            uuid=sys_uuid.UUID("66666666-6666-6666-6666-666666666666"),
            name="consumer",
            version="1.0.0",
            link="$consumer",
        )

        manifest = Manifest(
            uuid=sys_uuid.UUID("77777777-7777-7777-7777-777777777777"),
            name="test_manifest",
            version="1.0.0",
        )

        self.engine._namespaces["$test_export_node_1"] = type(
            "Namespace", (), {"element": export_element}
        )()
        self.engine._resource_exports[compute_export.full_link] = compute_resource
        self.engine._resource_exports[storage_export.full_link] = storage_resource

        result = self.engine.get_resource_by_export_link(
            manifest=manifest,
            from_element=import_element,
            link="$test_export_node_1.$core.compute.nodes.$test_node",
        )

        assert result is compute_resource
        assert result.value["type"] == "compute"
        assert result is not storage_resource
