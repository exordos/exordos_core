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

# A universal agent config persisted by a pre-border core image.
_OLD_UA_CONFIG = """\
[DEFAULT]
verbose = True


[universal_agent]
orch_endpoint = http://10.20.0.2:11013
caps_drivers =
    UserCapabilityDriver,
    PasswordCapabilityDriver,
    CoreDNSCertificateCapabilityDriver,
    LBAgentCapabilityDriver,
    GuestMachineCapabilityDriver,
    SSHKeyCapabilityDriver,
    RenderAgentDriver


[universal_agent_scheduler]
capabilities =
    em_*,
    password,
    certificate,
    paas_lb_agent,
    repo_proxy_installed_element


[CoreDNSCertificateCapabilityDriver]
username = admin
"""

# A universal agent config persisted by a pre-secret-driver core image
# (border driver already present, [UserCapabilityDriver] section exists).
_PRE_SECRET_DRIVER_UA_CONFIG = """\
[DEFAULT]
verbose = True


[universal_agent]
orch_endpoint = http://10.20.0.2:11013
caps_drivers =
    UserCapabilityDriver,
    PasswordCapabilityDriver,
    CoreDNSCertificateCapabilityDriver,
    LBAgentCapabilityDriver,
    BorderAgentCapabilityDriver,
    GuestMachineCapabilityDriver,
    SSHKeyCapabilityDriver,
    RenderAgentDriver


[universal_agent_scheduler]
capabilities =
    em_*,
    password,
    certificate,
    paas_lb_agent,
    repo_proxy_installed_element,
    border_agent


[CoreDNSCertificateCapabilityDriver]
username = admin
password = secret
user_api_base_url = http://localhost:80/api/core


[UserCapabilityDriver]
username = admin
password = secret
user_api_base_url = http://localhost:80/api/core
"""

# A core agent config persisted by a pre-idp core image.
_OLD_CORE_AGENT_CONFIG = """\
[DEFAULT]
verbose = True

[agent]
uuid5_name = core_agent

[models]
em_core_iam_permissions = exordos_core.user_api.iam.dm.models:Permission
em_core_iam_permissionbinding = exordos_core.user_api.iam.dm.models:PermissionBinding
em_core_iam_permission_bindings = exordos_core.user_api.iam.dm.models:PermissionBinding
em_core_vs_profiles = exordos_core.vs.dm.models:Profile

[filters]
em_core_dns_domains = project_id:12345678-c625-4fee-81d5-f691897b8142
em_core_dns_domains_records = project_id:12345678-c625-4fee-81d5-f691897b8142
em_core_vs_profiles = project_id:12345678-c625-4fee-81d5-f691897b8142
"""


def _run_ua(tmp_path):
    etc_path = tmp_path / "exordos_universal_agent.conf"
    data_path = tmp_path / "data" / "exordos_universal_agent.conf"
    with (
        mock.patch.object(bootstrap, "UA_CONFIG_PATH", str(etc_path)),
        mock.patch.object(bootstrap, "UA_CONFIG_DATA_PATH", str(data_path)),
        mock.patch.object(bootstrap.subprocess, "run") as run,
    ):
        bootstrap._ensure_ua_config_current()
    return etc_path, data_path, run


def _run_core_agent(tmp_path):
    etc_path = tmp_path / "core_agent.conf"
    data_path = tmp_path / "data" / "core_agent.conf"
    with (
        mock.patch.object(bootstrap, "CORE_AGENT_CONFIG_PATH", str(etc_path)),
        mock.patch.object(
            bootstrap, "CORE_AGENT_CONFIG_DATA_PATH", str(data_path)
        ),
        mock.patch.object(bootstrap.subprocess, "run") as run,
    ):
        bootstrap._ensure_core_agent_config_current()
    return etc_path, data_path, run


def test_upgrades_old_persisted_config(tmp_path):
    etc_path = tmp_path / "exordos_universal_agent.conf"
    etc_path.write_text(_OLD_UA_CONFIG, encoding="utf-8")

    etc_path, data_path, run = _run_ua(tmp_path)

    content = etc_path.read_text(encoding="utf-8")
    assert "    LBAgentCapabilityDriver,\n    BorderAgentCapabilityDriver,\n" in content
    assert "border_agent" in content
    # UserCapabilityDriver is migrated to SecretCapabilityDriver
    assert "UserCapabilityDriver" not in content
    assert "SecretCapabilityDriver" in content
    # Stand-specific values and the following section survive the rewrite
    assert "orch_endpoint = http://10.20.0.2:11013" in content
    assert "[CoreDNSCertificateCapabilityDriver]" in content
    # The persisted copy is kept in sync
    assert data_path.read_text(encoding="utf-8") == content
    run.assert_called_once()
    assert "try-restart" in run.call_args.args[0]


def test_migrates_user_capability_driver_section(tmp_path):
    etc_path = tmp_path / "exordos_universal_agent.conf"
    etc_path.write_text(_PRE_SECRET_DRIVER_UA_CONFIG, encoding="utf-8")

    etc_path, data_path, run = _run_ua(tmp_path)

    content = etc_path.read_text(encoding="utf-8")
    # caps_drivers updated
    assert "UserCapabilityDriver" not in content
    assert "    SecretCapabilityDriver,\n" in content
    # Section renamed and IAM endpoints appended
    assert "[SecretCapabilityDriver]" in content
    assert "[UserCapabilityDriver]" not in content
    assert "em_core_iam_users = /v1/iam/users/, password" in content
    assert "em_core_iam_clients = /v1/iam/clients/, secret" in content
    # Rendered password is preserved
    assert "password = secret" in content
    assert data_path.read_text(encoding="utf-8") == content
    run.assert_called_once()


def test_noop_on_current_config(tmp_path):
    etc_path = tmp_path / "exordos_universal_agent.conf"
    etc_path.write_text(_OLD_UA_CONFIG, encoding="utf-8")
    _run_ua(tmp_path)
    content = etc_path.read_text(encoding="utf-8")

    etc_path, data_path, run = _run_ua(tmp_path)

    assert etc_path.read_text(encoding="utf-8") == content
    run.assert_not_called()


def test_missing_ua_config_is_skipped(tmp_path):
    etc_path, data_path, run = _run_ua(tmp_path)

    assert not etc_path.exists()
    assert not data_path.exists()
    run.assert_not_called()


def test_upgrades_core_agent_config(tmp_path):
    etc_path = tmp_path / "core_agent.conf"
    etc_path.write_text(_OLD_CORE_AGENT_CONFIG, encoding="utf-8")

    etc_path, data_path, run = _run_core_agent(tmp_path)

    content = etc_path.read_text(encoding="utf-8")
    assert (
        "em_core_iam_idp = exordos_core.user_api.iam.dm.models:Idp" in content
    )
    assert (
        "em_core_iam_idp = project_id:12345678-c625-4fee-81d5-f691897b8142"
        in content
    )
    assert data_path.read_text(encoding="utf-8") == content
    run.assert_called_once()
    assert "ec-core-agent" in run.call_args.args[0]


def test_noop_on_current_core_agent_config(tmp_path):
    etc_path = tmp_path / "core_agent.conf"
    etc_path.write_text(_OLD_CORE_AGENT_CONFIG, encoding="utf-8")
    _run_core_agent(tmp_path)
    content = etc_path.read_text(encoding="utf-8")

    etc_path, data_path, run = _run_core_agent(tmp_path)

    assert etc_path.read_text(encoding="utf-8") == content
    run.assert_not_called()


def test_missing_core_agent_config_is_skipped(tmp_path):
    etc_path, data_path, run = _run_core_agent(tmp_path)

    assert not etc_path.exists()
    assert not data_path.exists()
    run.assert_not_called()
