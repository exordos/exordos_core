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

import typing as tp
import uuid as sys_uuid

import pytest

from exordos_core.repo import exceptions as repo_exceptions
from exordos_core.repo.builders import element as builder_element
from exordos_core.repo.dm import models

# ---------------------------------------------------------------------------
# Fake helpers
# ---------------------------------------------------------------------------


class FakeRepo:
    """Minimal fake Repository for sort-key tests."""

    def __init__(self, priority: int = 8192) -> None:
        self.priority = priority


class FakeElement:
    """Minimal fake RepoElement for helper-function tests.

    Only the attributes accessed by the helper functions are set.
    """

    def __init__(
        self,
        name: str = "test-element",
        version: str = "1.0.0",
        installation_state: str = models.RepoElementInstallationState.UNINSTALLED.value,
        repository: tp.Any = None,
        element: sys_uuid.UUID | None = None,
    ) -> None:
        self.name = name
        self.version = version
        self.installation_state = installation_state
        self.repository = repository or FakeRepo()
        self.element = element


# ---------------------------------------------------------------------------
# _parse_version
# ---------------------------------------------------------------------------


class TestParseVersion:
    def test_release_version(self):
        result = builder_element._parse_version("1.2.3")
        assert result == (1, 2, 3, True, "")

    def test_dev_version(self):
        result = builder_element._parse_version("0.1.12-dev")
        assert result == (0, 1, 12, False, "dev")

    def test_dev_version_with_build(self):
        result = builder_element._parse_version("1.0.0-rc1+build.123")
        assert result == (1, 0, 0, False, "rc1+build.123")

    def test_zero_version(self):
        result = builder_element._parse_version("0.0.0")
        assert result == (0, 0, 0, True, "")

    def test_large_version(self):
        result = builder_element._parse_version("99.99.99")
        assert result == (99, 99, 99, True, "")

    def test_invalid_version_missing_patch(self):
        with pytest.raises(ValueError, match="Invalid version format"):
            builder_element._parse_version("1.2")

    def test_invalid_version_non_numeric(self):
        with pytest.raises(ValueError, match="Invalid version format"):
            builder_element._parse_version("a.b.c")

    def test_invalid_version_empty(self):
        with pytest.raises(ValueError, match="Invalid version format"):
            builder_element._parse_version("")


# ---------------------------------------------------------------------------
# _version_key
# ---------------------------------------------------------------------------


class TestVersionKey:
    def test_release_version(self):
        assert builder_element._version_key("1.2.3") == (1, 2, 3, True, "")

    def test_dev_version(self):
        assert builder_element._version_key("1.2.3-dev") == (1, 2, 3, False, "dev")

    def test_ordering_release_greater_than_dev(self):
        release = builder_element._version_key("1.0.0")
        dev = builder_element._version_key("1.0.0-dev")
        assert release > dev

    def test_ordering_higher_major(self):
        v2 = builder_element._version_key("2.0.0")
        v1 = builder_element._version_key("1.0.0")
        assert v2 > v1


# ---------------------------------------------------------------------------
# _version_sort_key
# ---------------------------------------------------------------------------


class TestVersionSortKey:
    def test_release_sorts_before_dev(self):
        """Release versions should sort before dev versions (lower key = higher priority)."""
        release_key = builder_element._version_sort_key("1.0.0")
        dev_key = builder_element._version_sort_key("1.0.0-dev")
        assert release_key < dev_key

    def test_higher_version_sorts_before_lower(self):
        v2_key = builder_element._version_sort_key("2.0.0")
        v1_key = builder_element._version_sort_key("1.0.0")
        assert v2_key < v1_key

    def test_same_base_release_and_dev(self):
        keys = sorted(
            [
                builder_element._version_sort_key("1.0.0-dev"),
                builder_element._version_sort_key("1.0.0"),
                builder_element._version_sort_key("0.9.0"),
            ]
        )
        # Release versions sort before dev versions. Among releases,
        # higher versions sort first.
        # Expected order: 1.0.0 (release) < 0.9.0 (release) < 1.0.0-dev
        assert keys[0] == builder_element._version_sort_key("1.0.0")
        assert keys[1] == builder_element._version_sort_key("0.9.0")
        assert keys[2] == builder_element._version_sort_key("1.0.0-dev")


# ---------------------------------------------------------------------------
# _is_installation_in_progress
# ---------------------------------------------------------------------------


class TestIsInstallationInProgress:
    def test_installed(self):
        element = FakeElement(
            installation_state=models.RepoElementInstallationState.INSTALLED.value,
        )
        assert builder_element._is_installation_in_progress(element) is True

    def test_uninstalled(self):
        element = FakeElement(
            installation_state=models.RepoElementInstallationState.UNINSTALLED.value,
        )
        assert builder_element._is_installation_in_progress(element) is False


# ---------------------------------------------------------------------------
# _matches_version_constraint
# ---------------------------------------------------------------------------


class TestMatchesVersionConstraint:
    def test_eq_match(self):
        element = FakeElement(version="1.2.3")
        assert (
            builder_element._matches_version_constraint(element, {"==": "1.2.3"})
            is True
        )

    def test_eq_no_match(self):
        element = FakeElement(version="1.2.3")
        assert (
            builder_element._matches_version_constraint(element, {"==": "1.2.4"})
            is False
        )

    def test_gt_match(self):
        element = FakeElement(version="2.0.0")
        assert (
            builder_element._matches_version_constraint(element, {">": "1.0.0"}) is True
        )

    def test_gt_no_match_equal(self):
        element = FakeElement(version="1.0.0")
        assert (
            builder_element._matches_version_constraint(element, {">": "1.0.0"})
            is False
        )

    def test_ge_match_equal(self):
        element = FakeElement(version="1.0.0")
        assert (
            builder_element._matches_version_constraint(element, {">=": "1.0.0"})
            is True
        )

    def test_ge_match_greater(self):
        element = FakeElement(version="2.0.0")
        assert (
            builder_element._matches_version_constraint(element, {">=": "1.0.0"})
            is True
        )

    def test_ge_no_match(self):
        element = FakeElement(version="0.9.0")
        assert (
            builder_element._matches_version_constraint(element, {">=": "1.0.0"})
            is False
        )

    def test_lt_match(self):
        element = FakeElement(version="0.9.0")
        assert (
            builder_element._matches_version_constraint(element, {"<": "1.0.0"}) is True
        )

    def test_lt_no_match_equal(self):
        element = FakeElement(version="1.0.0")
        assert (
            builder_element._matches_version_constraint(element, {"<": "1.0.0"})
            is False
        )

    def test_le_match_equal(self):
        element = FakeElement(version="1.0.0")
        assert (
            builder_element._matches_version_constraint(element, {"<=": "1.0.0"})
            is True
        )

    def test_le_match_lower(self):
        element = FakeElement(version="0.9.0")
        assert (
            builder_element._matches_version_constraint(element, {"<=": "1.0.0"})
            is True
        )

    def test_le_no_match(self):
        element = FakeElement(version="2.0.0")
        assert (
            builder_element._matches_version_constraint(element, {"<=": "1.0.0"})
            is False
        )

    def test_release_vs_dev_constraint(self):
        """Release 1.0.0 should satisfy >= 1.0.0-dev."""
        element = FakeElement(version="1.0.0")
        assert (
            builder_element._matches_version_constraint(element, {">=": "1.0.0-dev"})
            is True
        )

    def test_dev_does_not_satisfy_release_constraint(self):
        """Dev 1.0.0-dev should NOT satisfy == 1.0.0."""
        element = FakeElement(version="1.0.0-dev")
        assert (
            builder_element._matches_version_constraint(element, {"==": "1.0.0"})
            is False
        )

    def test_unsupported_operator_raises(self):
        element = FakeElement(version="1.0.0")
        with pytest.raises(repo_exceptions.DependencyConstraintFormatError):
            builder_element._matches_version_constraint(element, {"!=": "1.0.0"})

    def test_multiple_unsupported_operators_raises(self):
        element = FakeElement(version="1.0.0")
        with pytest.raises(repo_exceptions.DependencyConstraintFormatError):
            builder_element._matches_version_constraint(
                element, {"!=": "1.0.0", "~=": "1.0.0"}
            )

    def test_eq_takes_precedence_over_other_operators(self):
        """== is checked first and short-circuits."""
        element = FakeElement(version="1.0.0")
        assert (
            builder_element._matches_version_constraint(
                element, {"==": "1.0.0", ">": "2.0.0"}
            )
            is True
        )

    def test_combined_gt_and_lt(self):
        element = FakeElement(version="1.5.0")
        assert (
            builder_element._matches_version_constraint(
                element, {">": "1.0.0", "<": "2.0.0"}
            )
            is True
        )

    def test_combined_gt_and_lt_no_match(self):
        element = FakeElement(version="3.0.0")
        assert (
            builder_element._matches_version_constraint(
                element, {">": "1.0.0", "<": "2.0.0"}
            )
            is False
        )

    def test_invalid_version_returns_false(self):
        element = FakeElement(version="invalid")
        assert (
            builder_element._matches_version_constraint(element, {">": "1.0.0"})
            is False
        )


# ---------------------------------------------------------------------------
# _element_sort_key
# ---------------------------------------------------------------------------


class TestElementSortKey:
    def test_installed_sorts_before_uninstalled(self):
        installed = FakeElement(
            installation_state=models.RepoElementInstallationState.INSTALLED.value,
        )
        uninstalled = FakeElement(
            installation_state=models.RepoElementInstallationState.UNINSTALLED.value,
        )
        assert builder_element._element_sort_key(
            installed
        ) < builder_element._element_sort_key(uninstalled)

    def test_higher_priority_sorts_first(self):
        high = FakeElement(repository=FakeRepo(priority=100))
        low = FakeElement(repository=FakeRepo(priority=1))
        assert builder_element._element_sort_key(
            high
        ) < builder_element._element_sort_key(low)

    def test_release_sorts_before_dev_same_priority(self):
        release = FakeElement(
            version="1.0.0",
            repository=FakeRepo(priority=8192),
        )
        dev = FakeElement(
            version="1.0.0-dev",
            repository=FakeRepo(priority=8192),
        )
        assert builder_element._element_sort_key(
            release
        ) < builder_element._element_sort_key(dev)

    def test_higher_version_sorts_first(self):
        v2 = FakeElement(version="2.0.0")
        v1 = FakeElement(version="1.0.0")
        assert builder_element._element_sort_key(
            v2
        ) < builder_element._element_sort_key(v1)


# ---------------------------------------------------------------------------
# RepoElementBuilderService state-check methods
# ---------------------------------------------------------------------------


class TestRequireMethods:
    def setup_method(self) -> None:
        self._service = builder_element.RepoElementBuilderService()

    def test_require_installation_true(self):
        element = FakeElement(
            installation_state=models.RepoElementInstallationState.INSTALLED.value,
            element=None,
        )
        assert self._service._require_installation(element) is True

    def test_require_installation_false_already_installed(self):
        element = FakeElement(
            installation_state=models.RepoElementInstallationState.INSTALLED.value,
            element=sys_uuid.uuid4(),
        )
        assert self._service._require_installation(element) is False

    def test_require_installation_false_uninstalled(self):
        element = FakeElement(
            installation_state=models.RepoElementInstallationState.UNINSTALLED.value,
            element=None,
        )
        assert self._service._require_installation(element) is False

    def test_require_upgrade_true(self):
        element = FakeElement(
            installation_state=models.RepoElementInstallationState.INSTALLED.value,
            element=sys_uuid.uuid4(),
        )
        assert self._service._require_upgrade(element) is True

    def test_require_upgrade_false_no_element(self):
        element = FakeElement(
            installation_state=models.RepoElementInstallationState.INSTALLED.value,
            element=None,
        )
        assert self._service._require_upgrade(element) is False

    def test_require_upgrade_false_uninstalled(self):
        element = FakeElement(
            installation_state=models.RepoElementInstallationState.UNINSTALLED.value,
            element=sys_uuid.uuid4(),
        )
        assert self._service._require_upgrade(element) is False

    def test_require_release_true(self):
        element = FakeElement(
            installation_state=models.RepoElementInstallationState.UNINSTALLED.value,
            element=sys_uuid.uuid4(),
        )
        assert self._service._require_release(element) is True

    def test_require_release_false_no_element(self):
        element = FakeElement(
            installation_state=models.RepoElementInstallationState.UNINSTALLED.value,
            element=None,
        )
        assert self._service._require_release(element) is False

    def test_require_release_false_installed(self):
        element = FakeElement(
            installation_state=models.RepoElementInstallationState.INSTALLED.value,
            element=sys_uuid.uuid4(),
        )
        assert self._service._require_release(element) is False

    def test_require_deletion_true(self):
        element = FakeElement(
            installation_state=models.RepoElementInstallationState.UNINSTALLED.value,
            element=None,
        )
        assert self._service._require_deletion(element) is True

    def test_require_deletion_false_with_element(self):
        element = FakeElement(
            installation_state=models.RepoElementInstallationState.UNINSTALLED.value,
            element=sys_uuid.uuid4(),
        )
        assert self._service._require_deletion(element) is False

    def test_require_deletion_false_installed(self):
        element = FakeElement(
            installation_state=models.RepoElementInstallationState.INSTALLED.value,
            element=None,
        )
        assert self._service._require_deletion(element) is False


# ---------------------------------------------------------------------------
# InstalledManifest
# ---------------------------------------------------------------------------


class TestInstalledManifest:
    def test_get_resource_kind(self):
        assert builder_element.InstalledManifest.get_resource_kind() == (
            "repo_proxy_installed_element"
        )

    def test_from_repo_element_with_manifest_uuid(self):
        manifest_uuid = sys_uuid.uuid4()
        project_id = sys_uuid.uuid4()
        element = FakeElement(version="1.0.0")
        element.uuid = manifest_uuid
        element.manifest = {"uuid": manifest_uuid, "key": "value"}
        element.project_id = project_id

        result = builder_element.InstalledManifest.from_repo_element(element)

        assert result.uuid == manifest_uuid
        assert result.name == element.name
        assert result.version == element.version
        assert result.manifest == element.manifest
        assert result.project_id == project_id

    def test_from_repo_element_without_manifest_uuid(self):
        """When manifest has no 'uuid', fall back to element.uuid."""
        element_uuid = sys_uuid.uuid4()
        element = FakeElement(version="2.0.0")
        element.uuid = element_uuid
        element.manifest = {"key": "value"}
        element.project_id = sys_uuid.uuid4()

        result = builder_element.InstalledManifest.from_repo_element(element)

        assert result.uuid == element_uuid
        assert result.name == element.name
        assert result.version == "2.0.0"

    def test_get_resource_target_fields(self):
        manifest = builder_element.InstalledManifest(
            name="test",
            version="1.0.0",
            project_id=sys_uuid.uuid4(),
        )
        fields = manifest.get_resource_target_fields()
        assert fields == frozenset(
            ("uuid", "name", "description", "version", "manifest", "project_id")
        )


# ---------------------------------------------------------------------------
# RepoElement (builder version)
# ---------------------------------------------------------------------------


class TestRepoElementBuilder:
    def test_get_resource_kind(self):
        assert builder_element.RepoElement.get_resource_kind() == "repo_proxy_element"

    def test_derivative_model_map(self):
        assert builder_element.RepoElement.__derivative_model_map__ == {
            "repo_proxy_installed_element": builder_element.InstalledManifest
        }
