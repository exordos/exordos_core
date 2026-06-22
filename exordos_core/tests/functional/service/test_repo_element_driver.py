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

import os
import tempfile
from unittest import mock
import uuid as sys_uuid

import pytest
from restalchemy.dm import filters as ra_filters
import yaml

from exordos_core.common import constants as c
from exordos_core.elements.dm import models as em_models
from exordos_core.repo.agents.universal.drivers import repo_element as driver
from exordos_core.repo.builders import element as re_builder
from exordos_core.repo.builders import repository as repo_builder
from exordos_core.repo.dm import models as repo_models


def _make_manifest(name, version, requirements=None):
    manifest = {"name": name, "version": version, "resources": {}}
    if requirements:
        manifest["requirements"] = requirements
    return manifest


def _write_manifest(dir_path, name, version, requirements=None):
    filename = f"{name}-{version}.yaml"
    filepath = os.path.join(dir_path, filename)
    manifest = _make_manifest(name, version, requirements)
    with open(filepath, "w") as f:
        yaml.safe_dump(manifest, f)
    return filepath


@pytest.fixture
def manifests_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def tf_storage(tmp_path):
    """Create a temporary target fields storage."""
    from gcl_sdk.agents.universal.storage import fs

    storage_path = os.path.join(str(tmp_path), "target_fields.json")
    return fs.TargetFieldsFileStorage(storage_path)


@pytest.fixture
def backend_client(tf_storage):
    """Create a RepoEmBackendClient with a temp storage."""
    return driver.RepoEmBackendClient(tf_storage=tf_storage)


@pytest.fixture
def bootstrap_repo_with_elements(manifests_dir, user_api):
    """Create a repository and run the builders to produce AVAILABLE elements."""
    _write_manifest(manifests_dir, "core", "1.0.0")
    _write_manifest(
        manifests_dir,
        "app",
        "1.0.0",
        requirements={"core": {"from_version": "1.0.0"}},
    )

    repo = repo_builder.Repository(
        name="test-driver-repo",
        description="Test driver repository",
        project_id=c.SERVICE_PROJECT_ID,
        status="NEW",
        sync_mode=repo_models.SyncMode.COPY.value,
        driver_spec=repo_models.BootstrapDriverSpec(
            manifests_dir=manifests_dir,
        ),
    )
    repo.insert()

    # Run repo builder to create elements
    repo_service = repo_builder.RepoProxyBuilderService()
    repo_service._iteration()

    # Run element builder to set AVAILABLE
    element_service = re_builder.RepoElementBuilderService()
    element_service._iteration()

    return repo


def _make_resource(installed_manifest):
    """Build a UA Resource from an InstalledManifest."""
    from gcl_sdk.agents.universal.dm import models as ua_models

    value = installed_manifest.dump_to_simple_view()
    return ua_models.Resource.from_value(value, kind=driver.KIND)


class TestRepoEmBackendClientCreate:
    """Functional tests for RepoEmBackendClient._create."""

    def test_create_installs_manifest(
        self, backend_client, bootstrap_repo_with_elements
    ):
        """_create should persist an EM Manifest and return an InstalledManifest with element UUID."""
        core = repo_models.RepoElement.objects.get_one(
            filters={
                "repository": bootstrap_repo_with_elements.uuid,
                "name": ra_filters.EQ("core"),
            },
        )

        installed = re_builder.InstalledManifest.from_repo_element(core)
        resource = _make_resource(installed)

        with mock.patch(
            "exordos_core.elements.dm.models.element_engine"
        ) as mock_engine:
            mock_engine.load_from_database.return_value = None
            mock_engine.add_element.return_value = None

            result = backend_client.create(resource)

        assert result.uuid == installed.uuid
        assert result.name == "core"
        assert result.version == "1.0.0"
        assert result.element is not None

        # Verify EM Manifest was persisted
        em_manifest = em_models.Manifest.objects.get_one(
            filters={"uuid": ra_filters.EQ(installed.uuid)},
        )
        assert em_manifest is not None
        assert em_manifest.name == "core"

        # Verify EM Element was created
        em_element = em_models.Element.objects.get_one(
            filters={"name": ra_filters.EQ("core")},
        )
        assert em_element is not None

    def test_create_upgrades_if_already_installed(
        self, backend_client, bootstrap_repo_with_elements
    ):
        """_update should upgrade an already installed element."""
        core = repo_models.RepoElement.objects.get_one(
            filters={
                "repository": bootstrap_repo_with_elements.uuid,
                "name": ra_filters.EQ("core"),
            },
        )

        installed = re_builder.InstalledManifest.from_repo_element(core)
        resource = _make_resource(installed)

        # First create (install)
        with mock.patch(
            "exordos_core.elements.dm.models.element_engine"
        ) as mock_engine:
            mock_engine.load_from_database.return_value = None
            mock_engine.add_element.return_value = None
            backend_client.create(resource)

        # Now update should work
        with mock.patch(
            "exordos_core.elements.dm.models.element_engine"
        ) as mock_engine:
            mock_engine.load_from_database.return_value = None
            mock_engine.add_element.return_value = None

            result = backend_client.update(resource)

        assert result.uuid == installed.uuid
        assert result.element is not None


class TestRepoEmBackendClientGet:
    """Functional tests for RepoEmBackendClient._get."""

    def test_get_returns_installed_manifest(
        self, backend_client, bootstrap_repo_with_elements
    ):
        """_get should return the InstalledManifest when the EM Manifest exists."""
        core = repo_models.RepoElement.objects.get_one(
            filters={
                "repository": bootstrap_repo_with_elements.uuid,
                "name": ra_filters.EQ("core"),
            },
        )

        installed = re_builder.InstalledManifest.from_repo_element(core)
        resource = _make_resource(installed)

        with mock.patch(
            "exordos_core.elements.dm.models.element_engine"
        ) as mock_engine:
            mock_engine.load_from_database.return_value = None
            mock_engine.add_element.return_value = None
            backend_client.create(resource)

        # Now get it
        result = backend_client.get(resource)
        assert result.uuid == installed.uuid
        assert result.name == "core"

    def test_get_raises_when_not_found(self, backend_client, user_api):
        """_get should raise ResourceNotFound when the EM Manifest does not exist."""
        from gcl_sdk.agents.universal.clients.backend import exceptions as backend_exc

        installed = re_builder.InstalledManifest(
            uuid=sys_uuid.uuid4(),
            name="nonexistent",
            version="1.0.0",
            project_id=c.SERVICE_PROJECT_ID,
            manifest={"name": "nonexistent", "version": "1.0.0", "resources": {}},
        )
        resource = _make_resource(installed)

        with pytest.raises(backend_exc.ResourceNotFound):
            backend_client.get(resource)


class TestRepoEmBackendClientList:
    """Functional tests for RepoEmBackendClient._list."""

    def test_list_returns_installed_elements(
        self, backend_client, bootstrap_repo_with_elements
    ):
        """_list should return all installed elements matching EM manifests."""
        core = repo_models.RepoElement.objects.get_one(
            filters={
                "repository": bootstrap_repo_with_elements.uuid,
                "name": ra_filters.EQ("core"),
            },
        )

        installed = re_builder.InstalledManifest.from_repo_element(core)
        resource = _make_resource(installed)

        with mock.patch(
            "exordos_core.elements.dm.models.element_engine"
        ) as mock_engine:
            mock_engine.load_from_database.return_value = None
            mock_engine.add_element.return_value = None
            backend_client.create(resource)

        # Mark core as installed in repo_elements
        core.installation_state = (
            repo_models.RepoElementInstallationState.INSTALLED.value
        )
        core.update()

        result = backend_client.list(driver.KIND)
        assert len(result) >= 1
        names = [r.name for r in result]
        assert "core" in names

    def test_list_raises_on_wrong_kind(self, backend_client, user_api):
        """_list should raise ValueError for unsupported kind."""
        with pytest.raises(ValueError):
            backend_client.list("wrong_kind")


class TestRepoEmBackendClientDelete:
    """Functional tests for RepoEmBackendClient._delete."""

    def test_delete_removes_manifest(
        self, backend_client, bootstrap_repo_with_elements
    ):
        """_delete should uninstall and remove the EM Manifest."""
        core = repo_models.RepoElement.objects.get_one(
            filters={
                "repository": bootstrap_repo_with_elements.uuid,
                "name": ra_filters.EQ("core"),
            },
        )

        installed = re_builder.InstalledManifest.from_repo_element(core)
        resource = _make_resource(installed)

        # Create first
        with mock.patch(
            "exordos_core.elements.dm.models.element_engine"
        ) as mock_engine:
            mock_engine.load_from_database.return_value = None
            mock_engine.add_element.return_value = None
            backend_client.create(resource)

        # Now delete
        with mock.patch(
            "exordos_core.elements.dm.models.element_engine"
        ) as mock_engine:
            mock_engine.load_from_database.return_value = None
            mock_engine.remove_element.return_value = None
            backend_client.delete(resource)

        # Manifest should be gone
        em_manifest = em_models.Manifest.objects.get_one_or_none(
            filters={"uuid": ra_filters.EQ(installed.uuid)},
        )
        assert em_manifest is None

    def test_delete_is_noop_when_not_found(self, backend_client, user_api):
        """_delete should be a no-op when the manifest does not exist."""
        installed = re_builder.InstalledManifest(
            uuid=sys_uuid.uuid4(),
            name="nonexistent",
            version="1.0.0",
            project_id=c.SERVICE_PROJECT_ID,
            manifest={"name": "nonexistent", "version": "1.0.0", "resources": {}},
        )
        resource = _make_resource(installed)

        # Should not raise
        backend_client.delete(resource)
