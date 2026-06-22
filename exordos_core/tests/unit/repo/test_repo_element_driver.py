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

from unittest import mock
import uuid as sys_uuid

from exordos_core.repo.agents.universal.drivers import repo_element as driver
from exordos_core.repo.builders import element as re_builder
from exordos_core.repo.dm import models as repo_models


class TestMakeInstalledManifest:
    """Tests for RepoEmBackendClient._make_installed_manifest."""

    def _make_client(self):
        storage = mock.MagicMock()
        return driver.RepoEmBackendClient(tf_storage=storage)

    def _make_em_manifest(self, name="core", version="1.0.0"):
        em_manifest = mock.MagicMock()
        em_manifest.uuid = sys_uuid.uuid4()
        em_manifest.name = name
        em_manifest.version = version
        em_manifest.description = "Test manifest"
        em_manifest.project_id = sys_uuid.UUID("00000000-0000-0000-0000-000000000000")
        em_manifest.api_version = "v1"
        em_manifest.schema_version = 1
        em_manifest.openapi_spec = None
        em_manifest.exports = {}
        em_manifest.imports = {}
        em_manifest.requirements = {}
        em_manifest.resources = {}
        return em_manifest

    def _make_em_element(self, name="core", version="1.0.0"):
        em_element = mock.MagicMock()
        em_element.uuid = sys_uuid.uuid4()
        em_element.name = name
        em_element.version = version
        return em_element

    def _make_repo_element(self, name="core", version="1.0.0"):
        repo_element = mock.MagicMock()
        repo_element.name = name
        repo_element.version = version
        repo_element.manifest = {
            "name": name,
            "version": version,
            "resources": {"test": {}},
        }
        return repo_element

    def test_with_repo_element_and_em_element(self):
        """Should use em_element when both repo_element and em_element are present."""
        client = self._make_client()
        em_manifest = self._make_em_manifest()
        em_element = self._make_em_element()

        result = client._make_installed_manifest(em_manifest, em_element)

        assert result.uuid == em_manifest.uuid
        assert result.name == em_manifest.name
        assert result.version == em_manifest.version
        assert result.description == em_manifest.description
        assert result.element == em_element.uuid
        assert result.status == repo_models.RepoElementStatus.ACTIVE.value
        assert result.project_id == em_manifest.project_id
        # Manifest should be built from em_manifest
        expected_manifest = {
            "name": em_manifest.name,
            "version": em_manifest.version,
            "api_version": em_manifest.api_version,
            "description": em_manifest.description,
            "schema_version": em_manifest.schema_version,
        }
        assert result.manifest == expected_manifest

    def test_with_repo_element_without_em_element(self):
        """Should use em_element=None when em_element is None."""
        client = self._make_client()
        em_manifest = self._make_em_manifest()

        result = client._make_installed_manifest(em_manifest, None)

        assert result.uuid == em_manifest.uuid
        assert result.element is None
        # Manifest should be built from em_manifest
        expected_manifest = {
            "name": em_manifest.name,
            "version": em_manifest.version,
            "api_version": em_manifest.api_version,
            "description": em_manifest.description,
            "schema_version": em_manifest.schema_version,
        }
        assert result.manifest == expected_manifest

    def test_without_repo_element_with_em_element(self):
        """Should use manifest from em_manifest when em_element is provided."""
        client = self._make_client()
        em_manifest = self._make_em_manifest()
        em_element = self._make_em_element()

        result = client._make_installed_manifest(em_manifest, em_element)

        assert result.uuid == em_manifest.uuid
        assert result.element == em_element.uuid
        # Manifest should be built from em_manifest
        expected_manifest = {
            "name": em_manifest.name,
            "version": em_manifest.version,
            "api_version": em_manifest.api_version,
            "description": em_manifest.description,
            "schema_version": em_manifest.schema_version,
        }
        assert result.manifest == expected_manifest

    def test_without_repo_element_without_em_element(self):
        """Should use manifest from em_manifest and element=None when em_element is None."""
        client = self._make_client()
        em_manifest = self._make_em_manifest()

        result = client._make_installed_manifest(em_manifest, None)

        assert result.uuid == em_manifest.uuid
        assert result.element is None
        # Manifest should be built from em_manifest
        expected_manifest = {
            "name": em_manifest.name,
            "version": em_manifest.version,
            "api_version": em_manifest.api_version,
            "description": em_manifest.description,
            "schema_version": em_manifest.schema_version,
        }
        assert result.manifest == expected_manifest


class TestMakeEmManifest:
    """Tests for RepoEmBackendClient._make_em_manifest."""

    def _make_client(self):
        storage = mock.MagicMock()
        return driver.RepoEmBackendClient(tf_storage=storage)

    def _make_installed(self, manifest=None):
        manifest = manifest or {
            "name": "core",
            "version": "1.0.0",
            "resources": {"res1": {}},
            "requirements": {"dep": {"from_version": "1.0.0"}},
            "exports": {"exp1": {}},
            "imports": {"imp1": {}},
            "openapi_spec": None,
        }
        return re_builder.InstalledManifest(
            uuid=sys_uuid.uuid4(),
            name="core",
            version="1.0.0",
            description="Test",
            project_id=sys_uuid.UUID("00000000-0000-0000-0000-000000000000"),
            manifest=manifest,
        )

    def test_extracts_all_fields(self):
        """Should extract all manifest fields from the nested dict."""
        client = self._make_client()
        installed = self._make_installed()

        result = client._make_em_manifest(installed)

        assert result.uuid == installed.uuid
        assert result.name == installed.name
        assert result.version == installed.version
        assert result.description == installed.manifest.get("description", "")
        assert result.project_id == installed.project_id
        assert result.schema_version == 1
        assert result.api_version == "v1"
        assert result.requirements == {"dep": {"from_version": "1.0.0"}}
        assert result.resources == {"res1": {}}
        assert result.exports == {"exp1": {}}
        assert result.imports == {"imp1": {}}
        assert result.openapi_spec is None

    def test_defaults_for_missing_fields(self):
        """Should use empty dicts for missing manifest fields."""
        client = self._make_client()
        installed = re_builder.InstalledManifest(
            uuid=sys_uuid.uuid4(),
            name="core",
            version="1.0.0",
            description="Test",
            project_id=sys_uuid.UUID("00000000-0000-0000-0000-000000000000"),
            manifest={"name": "core", "version": "1.0.0", "resources": {}},
        )

        result = client._make_em_manifest(installed)

        assert result.requirements == {}
        assert result.resources == {}
        assert result.exports == {}
        assert result.imports == {}
        assert result.openapi_spec is None


class TestRepoElementCapabilityDriver:
    """Tests for RepoElementCapabilityDriver."""

    def test_get_capabilities(self, tmp_path):
        """Should return the repo_proxy_installed_element capability."""
        d = driver.RepoElementCapabilityDriver(agent_work_dir=str(tmp_path))
        assert d.get_capabilities() == [driver.KIND]
        assert driver.KIND == "repo_proxy_installed_element"
