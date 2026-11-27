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
import subprocess
from unittest import mock

from oslo_config import cfg
import pytest

from exordos_core.cmd import bootstrap

_UA_TEMPLATE = (
    pathlib.Path(__file__).parents[4]
    / "etc/exordos_universal_agent/exordos_universal_agent.conf.j2"
)

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


@pytest.fixture(autouse=True)
def use_shipped_ua_template(monkeypatch):
    monkeypatch.setattr(bootstrap.c, "UA_CONFIG_TEMPLATE_PATH", str(_UA_TEMPLATE))


def _agent_config(path):
    conf = cfg.ConfigOpts()
    conf.register_opt(cfg.ListOpt("caps_drivers"), group="universal_agent")
    conf.register_opt(cfg.StrOpt("orch_endpoint"), group="universal_agent")
    conf(["--config-file", str(path)])
    return conf.universal_agent


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
    assert "BorderAgentCapabilityDriver" in _agent_config(etc_path).caps_drivers
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
    assert "restart" in run.call_args.args[0]


def test_migrates_user_capability_driver_section(tmp_path):
    etc_path = tmp_path / "exordos_universal_agent.conf"
    etc_path.write_text(_PRE_SECRET_DRIVER_UA_CONFIG, encoding="utf-8")

    etc_path, data_path, run = _run_ua(tmp_path)

    content = etc_path.read_text(encoding="utf-8")
    # caps_drivers updated
    assert "UserCapabilityDriver" not in content
    assert "SecretCapabilityDriver" in _agent_config(etc_path).caps_drivers
    # Section renamed and IAM endpoints appended
    assert "[SecretCapabilityDriver]" in content
    assert "[UserCapabilityDriver]" not in content
    assert "em_core_iam_users = /v1/iam/users/, password" in content
    assert "em_core_iam_clients = /v1/iam/clients/, secret" in content
    document = configparser.ConfigParser(interpolation=None)
    document.read(etc_path, encoding="utf-8")
    settings = document["SecretCapabilityDriver"]
    assert settings["username"] == "admin"
    assert settings["password"] == "secret"
    assert settings["user_api_base_url"] == "http://localhost:80/api/core"
    assert data_path.read_text(encoding="utf-8") == content
    run.assert_called_once()


@pytest.mark.parametrize(
    "username_option, clients_option",
    [
        ("username", "em_core_iam_clients"),
        ("Username", "EM_CORE_IAM_CLIENTS"),
    ],
)
def test_merges_coexisting_iam_driver_sections(
    tmp_path, username_option, clients_option
):
    etc_path = tmp_path / "exordos_universal_agent.conf"
    content = _PRE_SECRET_DRIVER_UA_CONFIG.replace(
        "    UserCapabilityDriver,\n", " UserCapabilityDriver,\n"
    ).replace("password = secret", "password = synthetic%%legacy")
    content += (
        "\n[SecretCapabilityDriver]\n"
        f"{username_option} = current-admin\n"
        f"{clients_option} = /v1/iam/clients/, secret, custom-filter\n"
    )
    etc_path.write_text(content, encoding="utf-8")

    _, data_path, restart = _run_ua(tmp_path)

    loaded = _agent_config(etc_path)
    assert loaded.caps_drivers.count("SecretCapabilityDriver") == 1
    assert "UserCapabilityDriver" not in loaded.caps_drivers
    document = configparser.ConfigParser(interpolation=None)
    document.read(etc_path, encoding="utf-8")
    assert not document.has_section("UserCapabilityDriver")
    settings = document["SecretCapabilityDriver"]
    assert settings["username"] == "current-admin"
    assert settings["password"] == "synthetic%%legacy"
    assert settings["em_core_iam_users"] == "/v1/iam/users/, password"
    assert settings["em_core_iam_clients"] == "/v1/iam/clients/, secret, custom-filter"
    assert data_path.read_bytes() == etc_path.read_bytes()
    restart.assert_called_once()
    _, _, restart = _run_ua(tmp_path)
    restart.assert_not_called()


@pytest.mark.parametrize(
    "drivers",
    [
        " CustomCapabilityDriver, SSHKeyCapabilityDriver",
        "\n    CustomCapabilityDriver,\n    SSHKeyCapabilityDriver",
        ' "CustomCapabilityDriver, SSHKeyCapabilityDriver"',
    ],
)
def test_upgrades_agent_config_without_render_driver(tmp_path, drivers):
    etc_path = tmp_path / "exordos_universal_agent.conf"
    etc_path.write_text(
        "[universal_agent]\n"
        f"caps_drivers ={drivers}\n"
        "orch_endpoint = http://core.example:11013\n"
        "\n[SecretCapabilityDriver]\n"
        "password = synthetic-test-value\n"
        "em_core_iam_users = /v1/iam/users/, password\n",
        encoding="utf-8",
    )

    old_mask = os.umask(0o022)
    try:
        _, persisted, restart = _run_ua(tmp_path)
    finally:
        os.umask(old_mask)

    configured = _agent_config(etc_path)
    stock = _agent_config(_UA_TEMPLATE).caps_drivers
    assert configured.caps_drivers == list(
        dict.fromkeys(["CustomCapabilityDriver", "SSHKeyCapabilityDriver", *stock])
    )
    assert configured.orch_endpoint == "http://core.example:11013"
    assert persisted.read_bytes() == etc_path.read_bytes()
    assert b"password = synthetic-test-value\n" in persisted.read_bytes()
    assert persisted.stat().st_mode & 0o777 == 0o600
    restart.assert_called_once()
    assert "exordos-universal-agent" in restart.call_args.args[0]

    _, _, restart = _run_ua(tmp_path)
    restart.assert_not_called()
    assert persisted.read_bytes() == etc_path.read_bytes()


@pytest.fixture
def legacy_ua_config(tmp_path, monkeypatch):
    active = tmp_path / "exordos_universal_agent.conf"
    saved = tmp_path / "data" / active.name
    legacy = _OLD_UA_CONFIG.replace("    RenderAgentDriver\n", "")
    active.write_text(legacy, encoding="utf-8")
    saved.parent.mkdir()
    saved.write_text(legacy, encoding="utf-8")
    restart = mock.Mock()
    monkeypatch.setattr(bootstrap, "UA_CONFIG_PATH", str(active))
    monkeypatch.setattr(bootstrap, "UA_CONFIG_DATA_PATH", str(saved))
    monkeypatch.setattr(bootstrap.subprocess, "run", restart)
    return active, saved, restart


def test_replaces_existing_readable_agent_config_copy(legacy_ua_config):
    active, saved, restart = legacy_ua_config
    saved.chmod(0o644)

    bootstrap._ensure_ua_config_current()

    assert b"RenderAgentDriver" in saved.read_bytes()
    assert saved.read_bytes() == active.read_bytes()
    assert saved.stat().st_mode & 0o777 == 0o600
    restart.assert_called_once()


def test_retries_agent_config_migration_after_persisted_write_error(legacy_ua_config):
    active, saved, restart = legacy_ua_config
    legacy = active.read_bytes()
    saved.unlink()
    saved.mkdir()

    with pytest.raises(OSError):
        bootstrap._ensure_ua_config_current()

    assert active.read_bytes() == legacy
    restart.assert_not_called()
    saved.rmdir()
    saved.write_bytes(legacy)

    bootstrap._ensure_ua_config_current()
    assert b"RenderAgentDriver" in saved.read_bytes()
    assert saved.read_bytes() == active.read_bytes()
    restart.assert_called_once()


def test_retries_failed_agent_restart_after_config_migration(legacy_ua_config):
    active, saved, restart = legacy_ua_config
    restart.side_effect = [subprocess.CalledProcessError(1, ["systemctl"]), None]

    with pytest.raises(subprocess.CalledProcessError):
        bootstrap._ensure_ua_config_current()

    assert b"RenderAgentDriver" in active.read_bytes()
    assert saved.read_bytes() == active.read_bytes()

    bootstrap._ensure_ua_config_current()
    bootstrap._ensure_ua_config_current()

    assert restart.call_count == 2
    restart.assert_called_with(
        [
            "systemctl",
            "restart",
            "exordos-universal-agent",
            "exordos-universal-scheduler",
        ],
        check=True,
    )


def test_bootstrap_retries_transient_agent_restart_failure(
    legacy_ua_config, tmp_path, monkeypatch
):
    active, saved, restart = legacy_ua_config
    restart.side_effect = [subprocess.CalledProcessError(1, ["systemctl"]), None]
    monkeypatch.setattr(bootstrap, "CONF", mock.Mock(retry_on_error=True))
    monkeypatch.setattr(bootstrap, "SPEC_PATH", str(tmp_path / "missing-spec.json"))
    monkeypatch.setattr(bootstrap.config, "parse", mock.Mock())
    monkeypatch.setattr(bootstrap.infra_log, "configure", mock.Mock())
    monkeypatch.setattr(
        bootstrap.engines.engine_factory, "configure_postgresql_factory", mock.Mock()
    )
    monkeypatch.setattr(bootstrap, "_migrate_installed_elements_to_repo", mock.Mock())
    next_step = mock.Mock()
    monkeypatch.setattr(bootstrap, "_ensure_core_agent_config_current", next_step)
    sleep = mock.Mock()
    monkeypatch.setattr(bootstrap.time, "sleep", sleep)

    bootstrap.main()

    assert restart.call_count == 2
    assert b"RenderAgentDriver" in active.read_bytes()
    assert active.read_bytes() == saved.read_bytes()
    sleep.assert_called_once()
    next_step.assert_called_once()


@pytest.mark.parametrize("config", [0, 1], ids=["active", "persisted"])
def test_agent_config_survives_interrupted_replacement(
    legacy_ua_config, config, monkeypatch
):
    target = legacy_ua_config[config]
    restart = legacy_ua_config[2]
    original = target.read_bytes()
    replace = os.replace

    def fail_before_replace(source, destination):
        if pathlib.Path(destination) == target:
            assert target.read_bytes() == original
            assert pathlib.Path(source).stat().st_mode & 0o777 == 0o600
            raise InterruptedError("interrupted before config replacement")
        replace(source, destination)

    monkeypatch.setattr(bootstrap.os, "replace", fail_before_replace)
    with pytest.raises(InterruptedError):
        bootstrap._ensure_ua_config_current()

    assert target.read_bytes() == original
    restart.assert_not_called()


def test_noop_on_current_config(tmp_path):
    etc_path = tmp_path / "exordos_universal_agent.conf"
    etc_path.write_text(_OLD_UA_CONFIG, encoding="utf-8")
    _run_ua(tmp_path)
    content = etc_path.read_text(encoding="utf-8")

    etc_path, data_path, run = _run_ua(tmp_path)

    assert etc_path.read_text(encoding="utf-8") == content
    run.assert_not_called()


def test_preserves_format_when_agent_config_matches_shipped_options(tmp_path):
    active = tmp_path / "exordos_universal_agent.conf"
    content = "# local operator comment\n" + _UA_TEMPLATE.read_text(encoding="utf-8")
    active.write_text(content, encoding="utf-8")

    _, saved, restart = _run_ua(tmp_path)

    assert active.read_text(encoding="utf-8") == content
    assert not saved.exists()
    restart.assert_not_called()


def test_adds_driver_list_without_existing_agent_section(tmp_path):
    active = tmp_path / "exordos_universal_agent.conf"
    content = _UA_TEMPLATE.read_text(encoding="utf-8")
    start = content.index("[universal_agent]")
    end = content.index("[universal_agent_scheduler]")
    active.write_text(content[:start] + content[end:], encoding="utf-8")

    _, saved, restart = _run_ua(tmp_path)

    assert (
        _agent_config(active).caps_drivers == _agent_config(_UA_TEMPLATE).caps_drivers
    )
    assert saved.read_bytes() == active.read_bytes()
    restart.assert_called_once()


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
