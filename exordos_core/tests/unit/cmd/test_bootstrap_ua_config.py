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
import pathlib
from unittest import mock

import pytest

from exordos_core.bootstrap import defaults as bootstrap
from exordos_core.cmd import bootstrap as cmd_bootstrap

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


def _run_ua(tmp_path, sdn_agent=False, with_overlay=True):
    etc_path = tmp_path / "exordos_universal_agent.conf"
    data_path = tmp_path / "data" / "exordos_universal_agent.conf"
    sdn_path = tmp_path / "sdn_agent.conf"
    if sdn_agent:
        sdn_path.write_text("[universal_agent]\n", encoding="utf-8")
    with (
        mock.patch.object(bootstrap, "UA_CONFIG_PATH", str(etc_path)),
        mock.patch.object(bootstrap, "UA_CONFIG_DATA_PATH", str(data_path)),
        mock.patch.object(bootstrap, "SDN_AGENT_CONFIG_PATH", str(sdn_path)),
        mock.patch.object(bootstrap.subprocess, "run") as run,
    ):
        bootstrap._ensure_ua_config_current(with_overlay)
    return etc_path, data_path, run


def _run_core_agent(tmp_path):
    etc_path = tmp_path / "core_agent.conf"
    data_path = tmp_path / "data" / "core_agent.conf"
    with (
        mock.patch.object(cmd_bootstrap, "CORE_AGENT_CONFIG_PATH", str(etc_path)),
        mock.patch.object(cmd_bootstrap, "CORE_AGENT_CONFIG_DATA_PATH", str(data_path)),
        mock.patch.object(cmd_bootstrap.subprocess, "run") as run,
    ):
        cmd_bootstrap._ensure_core_agent_config_current()
    return etc_path, data_path, run


def test_local_agent_uses_authenticated_apis_and_persists_key(tmp_path):
    config = tmp_path / "agent.conf"
    config.write_text(_OLD_UA_CONFIG)
    mirror = tmp_path / "data" / "agent.conf"
    key_path = tmp_path / "keys" / "private_key"
    node = mock.sentinel.node
    with (
        mock.patch.object(bootstrap, "UA_CONFIG_PATH", str(config)),
        mock.patch.object(bootstrap, "UA_CONFIG_DATA_PATH", str(mirror)),
        mock.patch.object(bootstrap.ua_constants, "PRIVATE_KEY_PATH", str(key_path)),
        mock.patch.object(bootstrap.ua_utils, "system_uuid", return_value=node),
        mock.patch.object(
            bootstrap.ua_models.NodeEncryptionKey, "get_or_create"
        ) as get_key,
        mock.patch.object(bootstrap.subprocess, "run") as run,
    ):
        get_key.return_value.private_key = "persisted-node-key"
        bootstrap.ensure_local_ua_transport()
        get_key.assert_called_once_with(node)
        parser = configparser.ConfigParser()
        parser.read(config)
        section = parser["universal_agent"]
        assert section.getboolean("orch_secure_communication")
        assert section["orch_endpoint"] == "http://127.0.0.1:11011"
        assert section["status_endpoint"] == "http://127.0.0.1:11012"
        assert section["private_key_path"] == str(key_path)
        assert key_path.read_text() == "persisted-node-key"
        assert key_path.stat().st_mode & 0o777 == 0o600
        assert config.read_text() == mirror.read_text()
        assert config.stat().st_mode & 0o777 == 0o600
        assert mirror.stat().st_mode & 0o777 == 0o600
        assert parser["CoreDNSCertificateCapabilityDriver"]["username"] == "admin"
        run.assert_called_once()
        run.reset_mock()
        before = config.read_text()
        config.chmod(0o644)
        mirror.chmod(0o644)
        bootstrap.ensure_local_ua_transport()
        assert config.read_text() == before
        assert config.stat().st_mode & 0o777 == 0o600
        assert mirror.stat().st_mode & 0o777 == 0o600
        run.assert_not_called()
        key_path.unlink()
        bootstrap.ensure_local_ua_transport()
        assert key_path.read_text() == "persisted-node-key"
        run.assert_called_once()


def test_unknown_overlay_preserves_config_and_persisted_copy(tmp_path):
    etc_path = tmp_path / "exordos_universal_agent.conf"
    data_path = tmp_path / "data" / "exordos_universal_agent.conf"
    content = _OLD_UA_CONFIG + "\n# persisted overlay capabilities\nevpn_agent\n"
    etc_path.write_text(content, encoding="utf-8")
    data_path.parent.mkdir()
    data_path.write_text(content, encoding="utf-8")

    _, _, run = _run_ua(tmp_path, with_overlay=None)

    assert etc_path.read_text(encoding="utf-8") == content
    assert data_path.read_text(encoding="utf-8") == content
    run.assert_not_called()


def test_main_rechecks_overlay_after_successful_startup_db(tmp_path):
    spec_path = tmp_path / "spec.json"
    spec_path.write_text('{"stand": {}}', encoding="utf-8")
    with (
        mock.patch.object(cmd_bootstrap, "SPEC_PATH", str(spec_path)),
        mock.patch.object(cmd_bootstrap.config, "parse"),
        mock.patch.object(cmd_bootstrap.infra_log, "configure"),
        mock.patch.object(
            cmd_bootstrap.engines.engine_factory, "configure_postgresql_factory"
        ),
        mock.patch.object(cmd_bootstrap, "_migrate_installed_elements_to_repo"),
        mock.patch.object(cmd_bootstrap, "_ensure_core_agent_config_current"),
        mock.patch.object(bootstrap, "save_developer_keys"),
        mock.patch.object(bootstrap, "apply_startup_db") as apply,
        mock.patch.object(bootstrap, "overlay_is_wanted", side_effect=[None, True]),
        mock.patch.object(cmd_bootstrap, "_ensure_ua_config_current") as ensure,
        mock.patch.object(
            bootstrap, "ensure_local_ua_transport", side_effect=SystemExit
        ),
    ):
        with pytest.raises(SystemExit):
            cmd_bootstrap.main()
    apply.assert_called_once_with({"stand": {}})
    assert ensure.call_args_list == [mock.call(None), mock.call(True)]


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
    # The evpn_node capability is wired in right after border, exactly once,
    # and its scheduler capabilities are advertised.
    assert (
        "    BorderAgentCapabilityDriver,\n    EvpnAgentCapabilityDriver,\n" in content
    )
    assert content.count("EvpnAgentCapabilityDriver") == 1
    assert "evpn_port" in content and "evpn_host" in content and "bgp_rr" in content
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
    assert "em_core_iam_idp = exordos_core.user_api.iam.dm.models:Idp" in content
    assert (
        "em_core_iam_idp = project_id:12345678-c625-4fee-81d5-f691897b8142" in content
    )
    for name, model in cmd_bootstrap._CORE_AGENT_SECTIONS["models"].items():
        assert f"{name} = {model}" in content
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

    result = cmd_bootstrap._ensure_section(
        content,
        "a",
        {"same": "1", "changed": "new", "dropped": None, "added": "2"},
    )

    assert result == (
        "[a]\n# keep = me\nsame=1\nchanged = new\nadded = 2\n\n[b]\ndropped = x\n"
    )


def test_ensure_section_appends_missing_section():
    result = cmd_bootstrap._ensure_section(
        "[a]\nkey = 1", "b", {"key": "2", "absent": None}
    )

    assert result == "[a]\nkey = 1\n\n[b]\nkey = 2\n"


def test_ensure_section_skips_missing_section_with_only_removals():
    assert cmd_bootstrap._ensure_section("[a]\n", "b", {"key": None}) == "[a]\n"


def test_a_node_with_its_own_sdn_agent_keeps_the_capability_released(tmp_path):
    """A hypervisor that wires guests into another installation's overlay has
    handed its evpn state to the dedicated SDN agent (`register-agent
    --write-config` strips the driver). Re-inserting it here would give the
    two agents one meta file again — silently, on the next boot."""
    etc_path = tmp_path / "exordos_universal_agent.conf"
    etc_path.write_text(_OLD_UA_CONFIG, encoding="utf-8")

    etc_path, _data_path, _run_mock = _run_ua(tmp_path, sdn_agent=True)

    content = etc_path.read_text(encoding="utf-8")
    assert "EvpnAgentCapabilityDriver" not in content
    # everything else this function maintains still lands
    assert "BorderAgentCapabilityDriver" in content
    assert "evpn_port" in content  # the scheduler section is node-independent


def test_an_installation_without_an_overlay_is_offered_no_evpn_capability(tmp_path):
    """A capability is an offer to be scheduled work of that kind, and an
    installation with no `ovs_evpn` network has none to offer. Advertising
    them anyway would mean an upgrade rewrote the agent's config and
    restarted it, on a stand that never asked for any of this."""
    etc_path = tmp_path / "exordos_universal_agent.conf"
    etc_path.write_text(_OLD_UA_CONFIG, encoding="utf-8")

    etc_path, _data_path, _run_mock = _run_ua(tmp_path, with_overlay=False)

    content = etc_path.read_text(encoding="utf-8")
    assert "EvpnAgentCapabilityDriver" not in content
    for capability in ("evpn_port", "evpn_host", "evpn_nf", "bgp_rr"):
        assert capability not in content
    # ... and everything this function maintained before the overlay existed
    # still lands, because that part is not conditional on anything.
    assert "BorderAgentCapabilityDriver" in content
    assert "border_agent" in content


def test_the_shipped_agent_config_offers_no_overlay_capability():
    """The image's agent config offers no overlay capability.

    A freshly flashed node starts from it without `_ensure_ua_config_current`'s
    gate, so it would advertise an overlay it may not have (an upgraded install
    came up with no guest store and zero pool capacity). Bootstrap adds the
    entries when an overlay was asked for.
    """
    import pathlib

    template = (
        pathlib.Path(__file__).parents[4]
        / "etc/exordos_universal_agent/exordos_universal_agent.conf.j2"
    )
    shipped = template.read_text(encoding="utf-8")

    assert "EvpnAgentCapabilityDriver" not in shipped
    for capability in bootstrap._UA_EVPN_CAPABILITIES:
        assert capability not in shipped, capability
    # ... and what the image does ship is what the non-overlay rewrite
    # produces, or an upgrade would silently take capabilities away.
    for capability in bootstrap._UA_CAPABILITIES:
        assert capability in shipped, capability


_NETWORK_MODELS = {
    "em_core_network_networks": "exordos_core.compute.dm.models:Network",
    "em_core_network_subnets": "exordos_core.compute.dm.models:Subnet",
    "em_core_network_catalog_addresses": (
        "exordos_core.user_api.network.dm.models:Address"
    ),
}


def _assert_network_models(content):
    config = configparser.ConfigParser(interpolation=None)
    config.read_string(content)
    for name, model in _NETWORK_MODELS.items():
        assert config["models"][name] == model
        assert name not in config["filters"]
        assert content.count(f"{name} = ") == 1


def test_an_upgraded_core_agent_config_gains_the_network_models(tmp_path):
    """A manifest naming `$core.network.networks` works after an upgrade.

    The config is restored from the data disk, so a line added to the
    template reaches only fresh installations unless it is added here.
    """
    etc_path = tmp_path / "core_agent.conf"
    etc_path.write_text(_OLD_CORE_AGENT_CONFIG, encoding="utf-8")

    etc_path, data_path, run = _run_core_agent(tmp_path)

    content = etc_path.read_text(encoding="utf-8")
    _assert_network_models(content)
    assert data_path.read_text(encoding="utf-8") == content
    run.assert_called_once()


def test_a_config_that_already_has_the_idp_still_gains_the_network_models(
    tmp_path,
):
    etc_path = tmp_path / "core_agent.conf"
    etc_path.write_text(_OLD_CORE_AGENT_CONFIG, encoding="utf-8")
    _run_core_agent(tmp_path)
    content = etc_path.read_text(encoding="utf-8")
    for name in _NETWORK_MODELS:
        content = "".join(
            line
            for line in content.splitlines(keepends=True)
            if not line.startswith(f"{name} = ")
        )
    etc_path.write_text(content, encoding="utf-8")

    etc_path, _data_path, run = _run_core_agent(tmp_path)

    content = etc_path.read_text(encoding="utf-8")
    _assert_network_models(content)
    assert content.count("em_core_iam_idp = ") == 2
    run.assert_called_once()


def test_the_template_carries_the_network_models_the_upgrade_adds():
    template = (
        pathlib.Path(cmd_bootstrap.__file__).parents[2]
        / "etc"
        / "exordos_core"
        / "core_agent.conf.j2"
    ).read_text(encoding="utf-8")
    for name in _NETWORK_MODELS:
        for section in ("models", "filters"):
            value = cmd_bootstrap._CORE_AGENT_SECTIONS[section][name]
            if value is not None:
                assert f"{name} = {value}" in template
