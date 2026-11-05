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

import configparser
import os
import pathlib
from unittest import mock

import pytest

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


# A core agent config persisted by an image that scoped the opaque secret
# capability to the service project.
_FILTERED_SECRET_CORE_AGENT_CONFIG = """\
[DEFAULT]
verbose = True

[agent]
uuid5_name = core_agent

[models]
em_core_iam_idp = exordos_core.user_api.iam.dm.models:Idp
em_core_secret_secrets = exordos_core.secret.dm.models:Secret
em_core_secret_passwords = exordos_core.secret.dm.models:Password

[filters]
em_core_secret_secrets = project_id:12345678-c625-4fee-81d5-f691897b8142
em_core_secret_passwords = project_id:12345678-c625-4fee-81d5-f691897b8142

[resource_transformer:em_core_secret_secrets]
ignore_null_attributes = True
attributes = value

[resource_transformer:em_core_secret_passwords]
ignore_null_attributes = True
attributes = value
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
        mock.patch.object(bootstrap, "CORE_AGENT_CONFIG_DATA_PATH", str(data_path)),
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


@pytest.mark.parametrize(
    "drivers",
    [
        "\n    CustomCapabilityDriver,\n    SSHKeyCapabilityDriver",
        "\n    CustomCapabilityDriver,\n    SSHKeyCapabilityDriver,",
        "CustomCapabilityDriver, SSHKeyCapabilityDriver",
        "\n    CustomCapabilityDriver,\n    # RenderAgentDriver is required after upgrade\n    SSHKeyCapabilityDriver",
        "\n    CustomCapabilityDriver,\n    SSHKeyCapabilityDriver # keep this comment",
    ],
)
def test_adds_missing_render_driver_and_preserves_custom_config(tmp_path, drivers):
    content = (
        "[universal_agent]\n"
        "orch_endpoint = https://example.invalid/orchestrator\n"
        f"caps_drivers = {drivers}\n\n"
        + bootstrap._UA_SCHEDULER_SECTION
        + "\n[CustomCapabilityDriver]\nusername = test-user\npassword = fixture-value\n"
    )
    etc_path = tmp_path / "exordos_universal_agent.conf"
    etc_path.write_text(content, encoding="utf-8")
    etc_path.chmod(0o600)

    etc_path, data_path, run = _run_ua(tmp_path)

    migrated = etc_path.read_text(encoding="utf-8")
    parsed = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    parsed.read_string(migrated)
    actual_drivers = [
        value.strip()
        for value in parsed.get("universal_agent", "caps_drivers").split(",")
        if value.strip()
    ]
    assert actual_drivers == [
        "CustomCapabilityDriver",
        "SSHKeyCapabilityDriver",
        "RenderAgentDriver",
    ]
    assert "orch_endpoint = https://example.invalid/orchestrator\n" in migrated
    assert migrated.endswith(
        "[CustomCapabilityDriver]\nusername = test-user\npassword = fixture-value\n"
    )
    for line in content.splitlines():
        if line.lstrip().startswith("#") or "# keep this comment" in line:
            assert line[line.index("#") :] in migrated
    assert etc_path.stat().st_mode & 0o777 == 0o600
    assert data_path.read_text(encoding="utf-8") == migrated
    run.assert_called_once_with(
        [
            "systemctl",
            "try-restart",
            "exordos-universal-agent",
            "exordos-universal-scheduler",
        ],
        check=True,
    )

    _, _, second_run = _run_ua(tmp_path)
    assert etc_path.read_text(encoding="utf-8") == migrated
    second_run.assert_not_called()


@pytest.mark.parametrize("indent", ["  ", "        ", "\t"])
def test_render_migration_preserves_indented_sibling_options(indent):
    content = (
        "[universal_agent]\n"
        f"{indent}caps_drivers = CustomCapabilityDriver, SSHKeyCapabilityDriver\n"
        f"{indent}orch_endpoint = https://example.invalid/orchestrator\n"
        f"{indent}status_endpoint = https://example.invalid/status\n"
        "\n[CustomCapabilityDriver]\nusername = test-user\n"
    )

    migrated = bootstrap._migrate_ua_render_driver(content)

    parsed = configparser.ConfigParser()
    parsed.read_string(migrated)
    drivers = [
        v.strip() for v in parsed.get("universal_agent", "caps_drivers").split(",")
    ]
    assert drivers == [
        "CustomCapabilityDriver",
        "SSHKeyCapabilityDriver",
        "RenderAgentDriver",
    ]
    assert (
        parsed.get("universal_agent", "orch_endpoint")
        == "https://example.invalid/orchestrator"
    )
    assert (
        parsed.get("universal_agent", "status_endpoint")
        == "https://example.invalid/status"
    )
    assert bootstrap._migrate_ua_render_driver(migrated) == migrated


@pytest.mark.parametrize("existing_mode", [None, 0o600, 0o644])
def test_persisted_config_copy_is_private(tmp_path, existing_mode):
    target = tmp_path / "data" / "agent.conf"
    if existing_mode is not None:
        target.parent.mkdir()
        target.write_text("old config", encoding="utf-8")
        target.chmod(existing_mode)
    previous_umask = os.umask(0o022)
    try:
        bootstrap._sync_config_to_data_path("fixture config", str(target))
    finally:
        os.umask(previous_umask)
    assert target.read_text(encoding="utf-8") == "fixture config"
    assert target.stat().st_mode & 0o777 == 0o600


def test_render_driver_migration_preserves_current_ua_template():
    template = (
        pathlib.Path(__file__).parents[4]
        / "etc/exordos_universal_agent/exordos_universal_agent.conf.j2"
    )
    content = template.read_text(encoding="utf-8")

    assert bootstrap._migrate_ua_render_driver(content) == content


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
    assert "em_core_iam_idp = exordos_core.user_api.iam.dm.models:Idp" in content
    assert (
        "em_core_iam_idp = project_id:12345678-c625-4fee-81d5-f691897b8142" in content
    )
    assert (
        "em_core_vs_profiles = exordos_core.vs.dm.models:Profile\n"
        "em_core_iam_idp = exordos_core.user_api.iam.dm.models:Idp\n"
        "em_core_secret_secrets = exordos_core.secret.dm.models:Secret\n"
        "em_core_iam_tokens = exordos_core.user_api.iam.dm.models:ManagedToken\n"
        "\n[filters]\n" in content
    )
    assert (
        "[resource_transformer:em_core_secret_secrets]\n"
        "ignore_null_attributes = True\n"
        "attributes = value\n" in content
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


def test_drops_secret_project_filter(tmp_path):
    etc_path = tmp_path / "core_agent.conf"
    etc_path.write_text(_FILTERED_SECRET_CORE_AGENT_CONFIG, encoding="utf-8")

    etc_path, data_path, run = _run_core_agent(tmp_path)

    content = etc_path.read_text(encoding="utf-8")
    assert (
        "em_core_secret_secrets = project_id:12345678-c625-4fee-81d5-f691897b8142"
        not in content
    )
    # The password filter and the secret model mapping are left alone
    assert (
        "em_core_secret_passwords = project_id:12345678-c625-4fee-81d5-f691897b8142"
        in content
    )
    assert "em_core_secret_secrets = exordos_core.secret.dm.models:Secret" in content
    assert data_path.read_text(encoding="utf-8") == content
    run.assert_called_once()


def test_noop_on_unfiltered_secret_core_agent_config(tmp_path):
    etc_path = tmp_path / "core_agent.conf"
    etc_path.write_text(_FILTERED_SECRET_CORE_AGENT_CONFIG, encoding="utf-8")
    _run_core_agent(tmp_path)
    content = etc_path.read_text(encoding="utf-8")

    etc_path, data_path, run = _run_core_agent(tmp_path)

    assert etc_path.read_text(encoding="utf-8") == content
    run.assert_not_called()


def test_noop_on_template_core_agent_config(tmp_path):
    template = pathlib.Path(__file__).parents[4] / "etc/exordos_core/core_agent.conf.j2"
    etc_path = tmp_path / "core_agent.conf"
    etc_path.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")

    etc_path, data_path, run = _run_core_agent(tmp_path)

    assert etc_path.read_text(encoding="utf-8") == template.read_text(encoding="utf-8")
    run.assert_not_called()


def test_ensure_section_sets_and_removes_keys():
    content = (
        "[a]\n# keep = me\nsame=1\nchanged = old\ndropped = x\n\n[b]\ndropped = x\n"
    )

    result = bootstrap._ensure_section(
        content,
        "a",
        {"same": "1", "changed": "new", "dropped": None, "added": "2"},
    )

    assert result == (
        "[a]\n# keep = me\nsame=1\nchanged = new\nadded = 2\n\n[b]\ndropped = x\n"
    )


def test_ensure_section_appends_missing_section():
    result = bootstrap._ensure_section(
        "[a]\nkey = 1", "b", {"key": "2", "absent": None}
    )

    assert result == "[a]\nkey = 1\n\n[b]\nkey = 2\n"


def test_ensure_section_skips_missing_section_with_only_removals():
    assert bootstrap._ensure_section("[a]\n", "b", {"key": None}) == "[a]\n"
