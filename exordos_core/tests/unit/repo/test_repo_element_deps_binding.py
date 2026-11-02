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

from exordos_core.common import exceptions as common_exc
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
        backup = mock.MagicMock()
        backup.element.name = "communal_pg_cluster_backup"
        backup.element.version = "2.6.4"
        ecosystem = mock.MagicMock()
        ecosystem.element.name = "exordos_ecosystem"
        ecosystem.element.version = "0.0.92"
        with mock.patch.object(
            models.RepoElementDepsBinding,
            "objects",
        ) as mock_objects:
            mock_objects.get_all.return_value = [ecosystem, backup, backup]

            with pytest.raises(common_exc.ValidateException) as error:
                models.RepoElement.uninstall(elem)

        assert str(error.value) == (
            "validate error: Element cannot be uninstalled: "
            "other elements depend on it: "
            "communal_pg_cluster_backup (2.6.4), "
            "exordos_ecosystem (0.0.92)"
        )
        elem.update.assert_not_called()
        assert elem.installation_state == (
            models.RepoElementInstallationState.INSTALLED.value
        )

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

        with pytest.raises(
            common_exc.ValidateException, match="Element must be installed"
        ):
            models.RepoElement.uninstall(elem)


# ---------------------------------------------------------------------------
# RepoElement.upgrade — installation handover
# ---------------------------------------------------------------------------


class TestUpgradeHandover:
    def _make_pair(self):
        installed = mock.MagicMock(spec=models.RepoElement)
        installed.uuid = sys_uuid.uuid4()
        installed.name = "empty"
        installed.installation_state = (
            models.RepoElementInstallationState.INSTALLED.value
        )
        installed.element = sys_uuid.uuid4()

        target = mock.MagicMock(spec=models.RepoElement)
        target.uuid = sys_uuid.uuid4()
        target.name = "empty"
        target.version = "2.0.0"
        target.installation_state = (
            models.RepoElementInstallationState.UNINSTALLED.value
        )
        target.element = None
        target.manifest = {"name": "empty"}
        target.dependencies = {}
        return installed, target

    def _upgrade(self, installed, target):
        with mock.patch.object(models.RepoElement, "objects") as element_objects:
            element_objects.get_one.return_value = target
            element_objects.get_all.return_value = []
            return models.RepoElement.upgrade(installed, target=str(target.uuid))

    def test_upgrade_returns_target_element(self):
        installed, target = self._make_pair()

        result = self._upgrade(installed, target)

        assert result is target
        assert target.installation_state == (
            models.RepoElementInstallationState.INSTALLED.value
        )

    def test_upgrade_moves_runtime_element_to_target(self):
        installed, target = self._make_pair()
        runtime_element = installed.element

        self._upgrade(installed, target)

        assert installed.installation_state == (
            models.RepoElementInstallationState.UNINSTALLED.value
        )
        # The runtime element reference stays until the builder releases the
        # old element, otherwise the live EM manifest is deleted too early.
        assert installed.element == runtime_element
        assert target.element == runtime_element

    def test_upgrade_not_installed_raises(self):
        elem = mock.MagicMock(spec=models.RepoElement)
        elem.element = None

        with pytest.raises(
            common_exc.ValidateException, match="Element must be installed to upgrade"
        ):
            models.RepoElement.upgrade(elem, target=str(sys_uuid.uuid4()))

    def test_upgrade_target_installed_raises(self):
        installed, target = self._make_pair()
        target.installation_state = models.RepoElementInstallationState.INSTALLED.value

        with mock.patch.object(models.RepoElement, "objects") as element_objects:
            element_objects.get_one.return_value = target

            with pytest.raises(
                common_exc.ValidateException, match="Target element must be uninstalled"
            ):
                models.RepoElement.upgrade(installed, target=str(target.uuid))

    def test_upgrade_rejects_incompatible_dependency_before_handover(self):
        installed, target = self._make_pair()
        target.dependencies = {"opencode_server": {">=": "0.2.4"}}
        dependency = mock.MagicMock(spec=models.RepoElement)
        dependency.name = "opencode_server"
        dependency.version = "0.2.1-dev+20261027111530.5c1c0ec1"
        dependency.installation_state = models.RepoElementInstallationState.INSTALLED
        runtime_element = installed.element

        with mock.patch.object(models.RepoElement, "objects") as objects:
            objects.get_one.return_value = target
            objects.get_all.return_value = [dependency]
            with pytest.raises(common_exc.ValidateException, match="opencode_server"):
                models.RepoElement.upgrade(installed, target=str(target.uuid))

        installed.update.assert_not_called()
        target.update.assert_not_called()
        assert installed.element == runtime_element
        assert (
            installed.installation_state
            == models.RepoElementInstallationState.INSTALLED
        )
        assert (
            target.installation_state == models.RepoElementInstallationState.UNINSTALLED
        )
        assert target.element is None

    @pytest.mark.parametrize("minimum,allowed", [("3.0.0", False), ("1.0.0", True)])
    def test_upgrade_checks_installed_dependents(self, minimum, allowed):
        installed, target = self._make_pair()
        target.version = "2.0.0"
        dependent = mock.MagicMock(spec=models.RepoElement)
        dependent.name = "consumer"
        dependent.version = "1.0.0"
        dependent.manifest = {"name": "consumer"}
        dependent.dependencies = {"empty": {">=": minimum}}

        with mock.patch.object(models.RepoElement, "objects") as objects:
            objects.get_one.return_value = target
            objects.get_all.return_value = [installed, dependent]
            if allowed:
                assert (
                    models.RepoElement.upgrade(installed, target=str(target.uuid))
                    is target
                )
            else:
                with pytest.raises(common_exc.ValidateException, match="consumer"):
                    models.RepoElement.upgrade(installed, target=str(target.uuid))
                installed.update.assert_not_called()
                target.update.assert_not_called()

    def test_upgrade_checks_transitive_dependencies(self):
        installed, target = self._make_pair()
        target.dependencies = {"middle": {">=": "1.0.0"}}
        middle = mock.MagicMock(spec=models.RepoElement)
        middle.name = "middle"
        middle.version = "1.0.0"
        middle.repository = mock.Mock(priority=8192)
        middle.manifest = {"name": "middle"}
        middle.dependencies = {"opencode_server": {">=": "0.2.4"}}
        middle.installation_state = models.RepoElementInstallationState.INSTALLED
        dependency = mock.MagicMock(spec=models.RepoElement)
        dependency.version = "0.2.1"
        dependency.installation_state = models.RepoElementInstallationState.INSTALLED
        with mock.patch.object(models.RepoElement, "objects") as objects:
            objects.get_one.return_value = target
            objects.get_all.side_effect = [[middle], [dependency]]
            with pytest.raises(common_exc.ValidateException, match="opencode_server"):
                models.RepoElement.upgrade(installed, target=str(target.uuid))
        installed.update.assert_not_called()
        target.update.assert_not_called()
