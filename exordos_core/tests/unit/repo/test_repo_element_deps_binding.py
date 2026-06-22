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

import pytest

from exordos_core.repo.dm import models

# ---------------------------------------------------------------------------
# RepoElementDepsBinding model
# ---------------------------------------------------------------------------


class TestRepoElementDepsBindingModel:
    def test_tablename(self):
        assert models.RepoElementDepsBinding.__tablename__ == (
            "repo_element_deps_bindings"
        )

    def test_has_element_relationship(self):
        props = dict(models.RepoElementDepsBinding.properties)
        assert "element" in props

    def test_has_depends_on_relationship(self):
        props = dict(models.RepoElementDepsBinding.properties)
        assert "depends_on" in props

    def test_has_uuid_property(self):
        props = dict(models.RepoElementDepsBinding.properties)
        assert "uuid" in props


# ---------------------------------------------------------------------------
# RepoElement.uninstall — dependency check
# ---------------------------------------------------------------------------


class TestUninstallDependencyCheck:
    def _make_installed_element(self):
        elem = mock.MagicMock(spec=models.RepoElement)
        elem.uuid = sys_uuid.uuid4()
        elem.installation_state = models.RepoElementInstallationState.INSTALLED.value
        elem.element = sys_uuid.uuid4()
        return elem

    def test_uninstall_rejected_when_dependents_exist(self):
        elem = self._make_installed_element()

        fake_binding = mock.MagicMock()
        with mock.patch.object(
            models.RepoElementDepsBinding,
            "objects",
        ) as mock_objects:
            mock_objects.get_all.return_value = [fake_binding]

            with pytest.raises(ValueError, match="other elements depend on it"):
                models.RepoElement.uninstall(elem)

    def test_uninstall_allowed_when_no_dependents(self):
        elem = self._make_installed_element()

        with mock.patch.object(
            models.RepoElementDepsBinding,
            "objects",
        ) as mock_objects:
            mock_objects.get_all.return_value = []

            models.RepoElement.uninstall(elem)

            assert elem.installation_state == (
                models.RepoElementInstallationState.UNINSTALLED.value
            )
            assert elem.element is None
            elem.update.assert_called_once()

    def test_uninstall_cleans_up_own_bindings(self):
        elem = self._make_installed_element()

        fake_binding_1 = mock.MagicMock()
        fake_binding_2 = mock.MagicMock()
        with mock.patch.object(
            models.RepoElementDepsBinding,
            "objects",
        ) as mock_objects:
            # First call: check dependents (none) -> empty
            # Second call: get own bindings -> two bindings to delete
            mock_objects.get_all.side_effect = [[], [fake_binding_1, fake_binding_2]]

            models.RepoElement.uninstall(elem)

            fake_binding_1.delete.assert_called_once()
            fake_binding_2.delete.assert_called_once()

    def test_uninstall_not_installed_raises(self):
        elem = mock.MagicMock(spec=models.RepoElement)
        elem.installation_state = models.RepoElementInstallationState.UNINSTALLED.value

        with pytest.raises(ValueError, match="Element must be installed"):
            models.RepoElement.uninstall(elem)
