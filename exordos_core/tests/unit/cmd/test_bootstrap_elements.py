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

import pytest

from exordos_core.cmd import bootstrap


def _element(version="1.0.0", priority=2048):
    return mock.Mock(
        version=version,
        status=bootstrap.repo_models.RepoElementStatus.AVAILABLE.value,
        repository=mock.Mock(priority=priority),
        installation_state=bootstrap.repo_models.RepoElementInstallationState.UNINSTALLED,
    )


@pytest.mark.parametrize("spec", [{}, {"elements": []}])
def test_no_elements(spec):
    with mock.patch.object(bootstrap.repo_models.RepoElement, "objects") as objects:
        bootstrap._install_elements_from_spec(spec)
    objects.get_all.assert_not_called()


def test_installs_named_elements():
    dbaas, s3aas = _element(), _element()
    with mock.patch.object(bootstrap.repo_models.RepoElement, "objects") as objects:
        objects.get_all.side_effect = [[], [dbaas], [], [s3aas]]
        bootstrap._install_elements_from_spec({"elements": ["dbaas", "s3aas"]})
    dbaas.install.assert_called_once_with()
    s3aas.install.assert_called_once_with()
    assert objects.get_all.call_args_list[0].kwargs["filters"][
        "name"
    ] == bootstrap.dm_filters.EQ("dbaas")
    assert objects.get_all.call_args_list[2].kwargs["filters"][
        "name"
    ] == bootstrap.dm_filters.EQ("s3aas")


def test_skips_installed_element():
    element = _element()
    with mock.patch.object(bootstrap.repo_models.RepoElement, "objects") as objects:
        objects.get_all.return_value = [element]
        bootstrap._install_elements_from_spec({"elements": ["dbaas"]})
    assert objects.get_all.call_count == 1
    element.install.assert_not_called()


def test_new_element_is_made_available_before_install():
    element = _element()
    element.status = bootstrap.repo_models.RepoElementStatus.NEW.value

    def check_available():
        assert element.status == bootstrap.repo_models.RepoElementStatus.AVAILABLE.value

    element.install.side_effect = check_available
    with mock.patch.object(bootstrap.repo_models.RepoElement, "objects") as objects:
        objects.get_all.side_effect = [[], [element]]
        bootstrap._install_elements_from_spec({"elements": ["dbaas"]})
    statuses = objects.get_all.call_args.kwargs["filters"]["status"]
    assert statuses == bootstrap.dm_filters.In(
        [
            bootstrap.repo_models.RepoElementStatus.NEW,
            bootstrap.repo_models.RepoElementStatus.AVAILABLE,
        ]
    )
    element.install.assert_called_once_with()


def test_missing_element_raises_for_bootstrap_retry():
    with mock.patch.object(bootstrap.repo_models.RepoElement, "objects") as objects:
        objects.get_all.return_value = []
        with pytest.raises(
            RuntimeError, match="Unable to find available element dbaas"
        ):
            bootstrap._install_elements_from_spec({"elements": ["dbaas"]})


def test_selects_by_priority_release_and_version():
    candidates = [
        _element("9.0.0", 1024),
        _element("9.0.0-dev", 2048),
        _element("1.0.0", 2048),
        _element("2.0.0", 2048),
    ]
    with mock.patch.object(bootstrap.repo_models.RepoElement, "objects") as objects:
        objects.get_all.side_effect = [[], candidates]
        bootstrap._install_elements_from_spec({"elements": ["dbaas"]})
    for element in candidates[:-1]:
        element.install.assert_not_called()
    candidates[-1].install.assert_called_once_with()


def test_install_failure_propagates_for_bootstrap_retry():
    element = _element()
    element.install.side_effect = RuntimeError("install failed")
    with mock.patch.object(bootstrap.repo_models.RepoElement, "objects") as objects:
        objects.get_all.side_effect = [[], [element]]
        with pytest.raises(RuntimeError, match="install failed"):
            bootstrap._install_elements_from_spec({"elements": ["dbaas"]})
