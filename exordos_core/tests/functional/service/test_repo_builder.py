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

import datetime
import os
import tempfile
import uuid as sys_uuid

from gcl_sdk.agents.universal import constants as ua_c
import pytest
from restalchemy.dm import filters as dm_filters
import yaml

from exordos_core.common import constants as c
from exordos_core.repo.builders import element as element_builder
from exordos_core.repo.builders import repository as repo_builder
from exordos_core.repo.dm import models as repo_models

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manifest(name: str, version: str, requirements: dict | None = None) -> dict:
    """Build a minimal valid manifest dict."""
    manifest = {
        "name": name,
        "version": version,
        "resources": {},
    }
    if requirements:
        manifest["requirements"] = requirements
    return manifest


def _write_manifest(
    dir_path: str, name: str, version: str, requirements: dict | None = None
) -> str:
    """Write a manifest YAML file and return the file path."""
    filename = f"{name}-{version}.yaml"
    filepath = os.path.join(dir_path, filename)
    manifest = _make_manifest(name, version, requirements)
    with open(filepath, "w") as f:
        yaml.safe_dump(manifest, f)
    return filepath


@pytest.fixture
def manifests_dir():
    """Create a temporary directory with test manifests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def bootstrap_repo(manifests_dir, user_api):
    """Create a Repository (builder version) with a bootstrap driver spec.

    Writes two manifests to the temp directory:
    - core 1.0.0 (no dependencies)
    - app 1.0.0 (depends on core >= 1.0.0)
    """
    _write_manifest(manifests_dir, "core", "1.0.0")
    _write_manifest(
        manifests_dir,
        "app",
        "1.0.0",
        requirements={"core": {"from_version": "1.0.0"}},
    )

    repo = repo_builder.Repository(
        name="test-repo",
        description="Test repository",
        project_id=c.ZERO_UUID,
        status=ua_c.InstanceStatus.NEW.value,
        sync_mode=repo_models.SyncMode.COPY.value,
        driver_spec=repo_models.BootstrapDriverSpec(
            manifests_dir=manifests_dir,
        ),
    )
    repo.insert()
    return repo


@pytest.fixture
def bootstrap_repo_lazy(manifests_dir, user_api):
    """Create a Repository (builder version) in LAZY sync mode."""
    _write_manifest(manifests_dir, "core", "1.0.0")
    _write_manifest(
        manifests_dir,
        "app",
        "1.0.0",
        requirements={"core": {"from_version": "1.0.0"}},
    )

    repo = repo_builder.Repository(
        name="test-repo-lazy",
        description="Test lazy repository",
        project_id=c.ZERO_UUID,
        status=ua_c.InstanceStatus.NEW.value,
        sync_mode=repo_models.SyncMode.LAZY.value,
        driver_spec=repo_models.BootstrapDriverSpec(
            manifests_dir=manifests_dir,
        ),
    )
    repo.insert()
    return repo


# ---------------------------------------------------------------------------
# RepoProxyBuilderService
# ---------------------------------------------------------------------------


class TestRepoProxyBuilderService:
    def setup_method(self) -> None:
        self._service = repo_builder.RepoProxyBuilderService()

    def teardown_method(self) -> None:
        pass

    def test_repository_get_resource_kind(self):
        assert repo_builder.Repository.get_resource_kind() == "repo_proxy_repository"

    def test_new_repository_iteration_copy_mode(self, bootstrap_repo):
        """_iteration should process new repository and save elements in COPY mode."""
        self._service._iteration()

        updated = repo_models.Repository.objects.get_one(
            filters={"uuid": dm_filters.EQ(bootstrap_repo.uuid)},
        )
        assert updated.status == repo_models.RepositoryStatus.ACTIVE.value

        elements = repo_models.RepoElement.objects.get_all(
            filters={"repository": bootstrap_repo.uuid},
        )
        assert len(elements) == 2
        names = {e.name for e in elements}
        assert names == {"core", "app"}

        # In COPY mode, manifests should be downloaded
        for elem in elements:
            assert elem.manifest != {}
            assert "name" in elem.manifest

    def test_new_repository_iteration_lazy_mode(self, bootstrap_repo_lazy):
        """_iteration should save elements in LAZY mode without manifests."""
        self._service._iteration()

        updated = repo_models.Repository.objects.get_one(
            filters={"uuid": dm_filters.EQ(bootstrap_repo_lazy.uuid)},
        )
        assert updated.status == repo_models.RepositoryStatus.ACTIVE.value

        elements = repo_models.RepoElement.objects.get_all(
            filters={"repository": bootstrap_repo_lazy.uuid},
        )
        assert len(elements) == 2
        names = {e.name for e in elements}
        assert names == {"core", "app"}

        # In LAZY mode, manifests should be empty
        for elem in elements:
            assert elem.manifest == {}

    def test_iteration_sets_next_refresh(self, manifests_dir, user_api):
        """_iteration should set next_refresh when refresh_rate is configured."""
        _write_manifest(manifests_dir, "core", "1.0.0")

        repo = repo_builder.Repository(
            name="test-refresh-repo",
            description="Test refresh repository",
            project_id=c.ZERO_UUID,
            status=ua_c.InstanceStatus.NEW.value,
            sync_mode=repo_models.SyncMode.COPY.value,
            refresh_rate=60,
            driver_spec=repo_models.BootstrapDriverSpec(
                manifests_dir=manifests_dir,
            ),
        )
        repo.insert()

        self._service._iteration()

        updated = repo_models.Repository.objects.get_one(
            filters={"uuid": dm_filters.EQ(repo.uuid)},
        )
        assert updated.next_refresh is not None
        now = datetime.datetime.now(datetime.timezone.utc)
        delta = updated.next_refresh - now
        assert 50 < delta.total_seconds() < 70

    def test_inventory_elements_persist_published(self, manifests_dir, user_api):
        """Inventory publication dates should be persisted or fall back to creation."""
        repo = repo_models.Repository(
            name="published-repo",
            description="Published repository",
            project_id=c.EM_PROJECT_ID,
            driver_spec=repo_models.BootstrapDriverSpec(manifests_dir=manifests_dir),
        )
        published = "2026-08-18T09:03:11.990399Z"
        elements = list(
            repo.iter_elements_in_inventory(
                {
                    "elements": {
                        "published": {"1.0.0": {"published": published}},
                        "fallback": {"1.0.0": {}},
                    }
                }
            )
        )
        published_element = next(e for e in elements if e.name == "published")
        fallback_element = next(e for e in elements if e.name == "fallback")

        assert published_element.published_at == datetime.datetime(
            2026, 8, 18, 9, 3, 11, 990399, tzinfo=datetime.timezone.utc
        )
        assert fallback_element.published_at == fallback_element.created_at

    def test_inventory_elements_persist_stable(self, manifests_dir, user_api):
        """Inventory elements should persist stability based on their version."""
        _write_manifest(manifests_dir, "element", "1.0.0")
        _write_manifest(manifests_dir, "element", "2.0.0rc1")
        repo = repo_builder.Repository(
            name="stable-repo",
            description="Stable repository",
            project_id=c.EM_PROJECT_ID,
            status=ua_c.InstanceStatus.NEW.value,
            sync_mode=repo_models.SyncMode.LAZY.value,
            driver_spec=repo_models.BootstrapDriverSpec(manifests_dir=manifests_dir),
        )
        repo.insert()

        self._service._iteration()

        elements = repo_models.RepoElement.objects.get_all(
            filters={"repository": repo.uuid},
        )
        stable_by_version = {element.version: element.stable for element in elements}
        assert stable_by_version == {"1.0.0": True, "2.0.0rc1": False}

        # `latest` must be set on initial creation too, not only after refresh,
        # and only the stable version may be marked latest.
        latest_by_version = {element.version: element.latest for element in elements}
        assert latest_by_version == {"1.0.0": True, "2.0.0rc1": False}

    def test_refresh_repository_updates_latest(self, manifests_dir, bootstrap_repo):
        """Refresh should select the greatest remaining version as latest."""
        self._service._iteration()
        self._service._refresh_repository(bootstrap_repo)
        core = repo_models.RepoElement.objects.get_one(
            filters={
                "repository": bootstrap_repo.uuid,
                "name": dm_filters.EQ("core"),
            },
        )
        assert core.latest is True

        _write_manifest(manifests_dir, "core", "2.0.0")
        repo_models.Repository.__driver_map__.clear()
        self._service._refresh_repository(bootstrap_repo)

        elements = repo_models.RepoElement.objects.get_all(
            filters={"repository": bootstrap_repo.uuid, "name": dm_filters.EQ("core")},
        )
        assert {element.version: element.latest for element in elements} == {
            "1.0.0": False,
            "2.0.0": True,
        }
        assert {element.version: element.stable for element in elements} == {
            "1.0.0": True,
            "2.0.0": True,
        }

        _write_manifest(manifests_dir, "core", "3.0.0rc1")
        repo_models.Repository.__driver_map__.clear()
        self._service._refresh_repository(bootstrap_repo)

        elements = repo_models.RepoElement.objects.get_all(
            filters={"repository": bootstrap_repo.uuid, "name": dm_filters.EQ("core")},
        )
        assert {element.version: element.latest for element in elements} == {
            "1.0.0": False,
            "2.0.0": True,
            "3.0.0rc1": False,
        }

        os.remove(os.path.join(manifests_dir, "core-2.0.0.yaml"))
        os.remove(os.path.join(manifests_dir, "core-3.0.0rc1.yaml"))
        repo_models.Repository.__driver_map__.clear()
        self._service._refresh_repository(bootstrap_repo)

        core = repo_models.RepoElement.objects.get_one(
            filters={
                "repository": bootstrap_repo.uuid,
                "name": dm_filters.EQ("core"),
            },
        )
        assert core.version == "1.0.0"
        assert core.latest is True

    def test_refresh_repository_removes_old_elements(
        self, manifests_dir, bootstrap_repo
    ):
        """_refresh_repository should remove elements no longer in inventory."""
        # Initial sync via iteration
        self._service._iteration()

        # Remove the app manifest
        os.remove(os.path.join(manifests_dir, "app-1.0.0.yaml"))

        # Clear driver cache so the removal is discovered
        repo_models.Repository.__driver_map__.clear()

        # Refresh
        repo = repo_models.Repository.objects.get_one(
            filters={"uuid": dm_filters.EQ(bootstrap_repo.uuid)},
        )
        self._service._refresh_repository(repo)

        elements = repo_models.RepoElement.objects.get_all(
            filters={"repository": bootstrap_repo.uuid},
        )
        names = {e.name for e in elements}
        assert "app" not in names
        assert "core" in names

    def test_refresh_repository_keeps_installed_elements(
        self, manifests_dir, bootstrap_repo
    ):
        """_refresh_repository should not remove installed elements even if gone from inventory."""
        # Initial sync via iteration
        self._service._iteration()

        # Install the app element
        app = repo_models.RepoElement.objects.get_one(
            filters={
                "repository": bootstrap_repo.uuid,
                "name": dm_filters.EQ("app"),
            },
        )
        app.install()

        # Remove the app manifest
        os.remove(os.path.join(manifests_dir, "app-1.0.0.yaml"))

        # Clear driver cache so the removal is discovered
        repo_models.Repository.__driver_map__.clear()

        # Refresh
        repo = repo_models.Repository.objects.get_one(
            filters={"uuid": dm_filters.EQ(bootstrap_repo.uuid)},
        )
        self._service._refresh_repository(repo)

        elements = repo_models.RepoElement.objects.get_all(
            filters={"repository": bootstrap_repo.uuid},
        )
        names = {e.name for e in elements}
        # app should still be there because it's installed
        assert "app" in names
        assert "core" in names

    def test_refresh_repository_retires_installed_element(
        self, manifests_dir, bootstrap_repo
    ):
        """An installed element gone from inventory must leave the catalogue."""
        self._service._iteration()

        _write_manifest(manifests_dir, "core", "2.0.0")
        repo_models.Repository.__driver_map__.clear()
        self._service._refresh_repository(bootstrap_repo)

        installed = repo_models.RepoElement.objects.get_one(
            filters={
                "repository": bootstrap_repo.uuid,
                "name": dm_filters.EQ("core"),
                "version": dm_filters.EQ("2.0.0"),
            },
        )
        assert installed.latest is True
        installed.install()

        os.remove(os.path.join(manifests_dir, "core-2.0.0.yaml"))
        repo_models.Repository.__driver_map__.clear()
        self._service._refresh_repository(bootstrap_repo)

        elements = repo_models.RepoElement.objects.get_all(
            filters={"repository": bootstrap_repo.uuid, "name": dm_filters.EQ("core")},
        )
        by_version = {element.version: element for element in elements}

        # The installed row survives, but no longer counts as catalogue entry.
        assert set(by_version) == {"1.0.0", "2.0.0"}
        assert by_version["2.0.0"].latest is False
        assert by_version["2.0.0"].stable is False

        # ... and the greatest version still on offer takes its place.
        assert by_version["1.0.0"].latest is True
        assert by_version["1.0.0"].stable is True

    def test_check_refresh_skips_without_refresh_rate(self, bootstrap_repo):
        """_check_refresh should skip repositories without refresh_rate."""
        bootstrap_repo.refresh_rate = 0
        bootstrap_repo.update()

        # Should not raise
        self._service._check_refresh()

    def test_check_refresh_skips_when_not_due(self, bootstrap_repo):
        """_check_refresh should skip repositories where next_refresh is in the future."""
        bootstrap_repo.refresh_rate = 3600
        bootstrap_repo.next_refresh = datetime.datetime.now(
            datetime.timezone.utc
        ) + datetime.timedelta(hours=1)
        bootstrap_repo.update()

        # Should not raise or force update
        self._service._check_refresh()


# ---------------------------------------------------------------------------
# RepoElementBuilderService
# ---------------------------------------------------------------------------


class TestRepoElementBuilderService:
    def setup_method(self) -> None:
        self._service = element_builder.RepoElementBuilderService()

    def teardown_method(self) -> None:
        pass

    def test_element_get_resource_kind(self):
        assert element_builder.RepoElement.get_resource_kind() == "repo_proxy_element"

    def test_new_element_iteration(self, bootstrap_repo):
        """_iteration should process new elements and mark them AVAILABLE."""
        # First, run repo builder to create elements
        repo_service = repo_builder.RepoProxyBuilderService()
        repo_service._iteration()

        # Now run element builder
        self._service._iteration()

        elements = repo_models.RepoElement.objects.get_all(
            filters={"repository": bootstrap_repo.uuid},
        )
        for elem in elements:
            assert elem.status == repo_models.RepoElementStatus.AVAILABLE.value

    def test_collect_dependencies_no_deps(self, bootstrap_repo):
        """_collect_dependencies should return just the element when no deps."""
        # Run repo builder to create elements
        repo_service = repo_builder.RepoProxyBuilderService()
        repo_service._iteration()

        core = repo_models.RepoElement.objects.get_one(
            filters={
                "repository": bootstrap_repo.uuid,
                "name": dm_filters.EQ("core"),
            },
        )

        result = self._service._collect_dependencies(core)
        assert len(result) == 1
        assert result[0].name == "core"

    def test_collect_dependencies_with_dep(self, bootstrap_repo):
        """_collect_dependencies should return deps + the element itself."""
        # Run repo builder to create elements
        repo_service = repo_builder.RepoProxyBuilderService()
        repo_service._iteration()

        app = repo_models.RepoElement.objects.get_one(
            filters={
                "repository": bootstrap_repo.uuid,
                "name": dm_filters.EQ("app"),
            },
        )

        result = self._service._collect_dependencies(app)
        # core comes before app (dependency order), app is last
        names = [e.name for e in result]
        assert names[-1] == "app"
        assert "core" in names

    def test_collect_dependencies_circular(self, manifests_dir, bootstrap_repo):
        """_collect_dependencies should handle circular dependencies."""
        _write_manifest(
            manifests_dir,
            "circular_a",
            "1.0.0",
            requirements={"circular_b": {"from_version": "1.0.0"}},
        )
        _write_manifest(
            manifests_dir,
            "circular_b",
            "1.0.0",
            requirements={"circular_a": {"from_version": "1.0.0"}},
        )

        # Run repo builder to create elements
        repo_service = repo_builder.RepoProxyBuilderService()
        repo_service._iteration()

        elem_a = repo_models.RepoElement.objects.get_one(
            filters={
                "repository": bootstrap_repo.uuid,
                "name": dm_filters.EQ("circular_a"),
            },
        )

        # Should not hang
        result = self._service._collect_dependencies(elem_a)
        assert elem_a in result

    def test_collect_dependencies_not_found(self, manifests_dir, bootstrap_repo):
        """_collect_dependencies should raise DependencyNotFoundError when dep is missing."""
        _write_manifest(
            manifests_dir,
            "missing_dep",
            "1.0.0",
            requirements={"nonexistent": {"from_version": "1.0.0"}},
        )

        # Run repo builder to create elements
        repo_service = repo_builder.RepoProxyBuilderService()
        repo_service._iteration()

        elem = repo_models.RepoElement.objects.get_one(
            filters={
                "repository": bootstrap_repo.uuid,
                "name": dm_filters.EQ("missing_dep"),
            },
        )

        from exordos_core.repo import exceptions as repo_exceptions

        with pytest.raises(repo_exceptions.DependencyNotFoundError):
            self._service._collect_dependencies(elem)

    def test_install_element_via_iteration(self, bootstrap_repo):
        """Full iteration should install an element marked as INSTALLED."""
        # Run repo builder to create elements
        repo_service = repo_builder.RepoProxyBuilderService()
        repo_service._iteration()

        # First iteration: creates TargetResource, sets AVAILABLE
        self._service._iteration()

        # Mark core as installed (triggers update on next iteration)
        core = repo_models.RepoElement.objects.get_one(
            filters={
                "repository": bootstrap_repo.uuid,
                "name": dm_filters.EQ("core"),
            },
        )
        core.installation_state = (
            repo_models.RepoElementInstallationState.INSTALLED.value
        )
        core.update()

        # Second iteration: processes the update (install)
        self._service._iteration()

        updated = repo_models.RepoElement.objects.get_one(
            filters={"uuid": dm_filters.EQ(core.uuid)},
        )
        assert updated.status == repo_models.RepoElementStatus.IN_PROGRESS.value

    def test_install_element_with_dependency_via_iteration(self, bootstrap_repo):
        """Installing app should also install its dependency (core)."""
        # Run repo builder to create elements
        repo_service = repo_builder.RepoProxyBuilderService()
        repo_service._iteration()

        # First iteration: creates TargetResource, sets AVAILABLE
        self._service._iteration()

        # Mark app as installed (triggers update on next iteration)
        app = repo_models.RepoElement.objects.get_one(
            filters={
                "repository": bootstrap_repo.uuid,
                "name": dm_filters.EQ("app"),
            },
        )
        app.installation_state = (
            repo_models.RepoElementInstallationState.INSTALLED.value
        )
        app.update()

        # Second iteration: processes the update (install with deps)
        self._service._iteration()

        # Both core and app should be in progress
        core = repo_models.RepoElement.objects.get_one(
            filters={
                "repository": bootstrap_repo.uuid,
                "name": dm_filters.EQ("core"),
            },
        )
        app_updated = repo_models.RepoElement.objects.get_one(
            filters={"uuid": dm_filters.EQ(app.uuid)},
        )
        assert core.installation_state == (
            repo_models.RepoElementInstallationState.INSTALLED.value
        )
        assert app_updated.status == repo_models.RepoElementStatus.IN_PROGRESS.value

    def test_release_element_via_iteration(self, bootstrap_repo):
        """Releasing an installed element should clear element link.

        We test the release flow by calling update_instance_derivatives
        directly. Since `element` is a DB property with a FK constraint
        to em_elements, we can't set it to a random UUID. Instead we
        mock the property on the class to simulate a linked element.
        """
        from unittest import mock

        from gcl_sdk.agents.universal.dm import models as ua_models

        # Run repo builder to create elements
        repo_service = repo_builder.RepoProxyBuilderService()
        repo_service._iteration()

        # First iteration: creates TargetResource, sets AVAILABLE
        self._service._iteration()

        # Get core and simulate installed state
        core = repo_models.RepoElement.objects.get_one(
            filters={
                "repository": bootstrap_repo.uuid,
                "name": dm_filters.EQ("core"),
            },
        )
        core.installation_state = (
            repo_models.RepoElementInstallationState.INSTALLED.value
        )
        core.update()

        # Set to uninstalled (triggers release in update_instance_derivatives)
        core.installation_state = (
            repo_models.RepoElementInstallationState.UNINSTALLED.value
        )

        # Mock the element property to simulate a linked EM element.
        # _require_release checks: installation_state == UNINSTALLED
        # and element is not None. update_instance_derivatives sets
        # element=None, so the mock property needs a setter.
        fake_element_uuid = sys_uuid.uuid4()

        def _element_getter(self):
            return fake_element_uuid

        def _element_setter(self, value):
            pass

        element_mock = property(_element_getter, _element_setter)
        with mock.patch.object(
            repo_models.RepoElement,
            "element",
            element_mock,
        ):
            resource = ua_models.TargetResource(
                uuid=core.uuid,
                kind=element_builder.RepoElement.get_resource_kind(),
            )
            result = self._service.update_instance_derivatives(
                core,
                resource,
                [],
            )

        assert len(result) == 0
        assert core.status == repo_models.RepoElementStatus.AVAILABLE.value
