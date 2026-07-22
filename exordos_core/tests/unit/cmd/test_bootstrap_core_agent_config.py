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

from exordos_core.cmd import bootstrap

_OLD_CORE_AGENT_CONFIG = """\
[models]
em_core_iam_roles = exordos_core.user_api.iam.dm.models:Role
em_core_iam_rolebinding = exordos_core.user_api.iam.dm.models:RoleBinding
em_core_iam_permissions = exordos_core.user_api.iam.dm.models:Permission
em_core_iam_permissionbinding = exordos_core.user_api.iam.dm.models:PermissionBinding
"""


def _run(tmp_path):
    etc_path = tmp_path / "core_agent.conf"
    data_path = tmp_path / "data" / "core_agent.conf"
    with (
        mock.patch.object(bootstrap, "CORE_AGENT_CONFIG_PATH", str(etc_path)),
        mock.patch.object(bootstrap, "CORE_AGENT_CONFIG_DATA_PATH", str(data_path)),
        mock.patch.object(bootstrap.subprocess, "run") as run,
    ):
        bootstrap._ensure_core_agent_config_current()
    return etc_path, data_path, run


def test_adds_canonical_iam_binding_kinds_to_old_config(tmp_path):
    etc_path = tmp_path / "core_agent.conf"
    etc_path.write_text(_OLD_CORE_AGENT_CONFIG, encoding="utf-8")

    etc_path, data_path, run = _run(tmp_path)

    content = etc_path.read_text(encoding="utf-8")
    assert "em_core_iam_role_bindings =" in content
    assert "em_core_iam_permission_bindings =" in content
    assert "em_core_iam_rolebinding =" in content
    assert "em_core_iam_permissionbinding =" in content
    assert data_path.read_text(encoding="utf-8") == content
    run.assert_called_once_with(
        ["systemctl", "try-restart", "ec-core-agent"], check=True
    )


def test_noop_on_current_config(tmp_path):
    etc_path = tmp_path / "core_agent.conf"
    etc_path.write_text(_OLD_CORE_AGENT_CONFIG, encoding="utf-8")
    _run(tmp_path)
    content = etc_path.read_text(encoding="utf-8")

    etc_path, data_path, run = _run(tmp_path)

    assert etc_path.read_text(encoding="utf-8") == content
    assert data_path.read_text(encoding="utf-8") == content
    run.assert_not_called()


def test_missing_config_is_skipped(tmp_path):
    etc_path, data_path, run = _run(tmp_path)

    assert not etc_path.exists()
    assert not data_path.exists()
    run.assert_not_called()
