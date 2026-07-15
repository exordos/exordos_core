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


def _run(tmp_path):
    etc_path = tmp_path / "exordos_universal_agent.conf"
    data_path = tmp_path / "data" / "exordos_universal_agent.conf"
    with (
        mock.patch.object(bootstrap, "UA_CONFIG_PATH", str(etc_path)),
        mock.patch.object(bootstrap, "UA_CONFIG_DATA_PATH", str(data_path)),
        mock.patch.object(bootstrap.subprocess, "run") as run,
    ):
        bootstrap._ensure_ua_config_current()
    return etc_path, data_path, run


def test_upgrades_old_persisted_config(tmp_path):
    etc_path = tmp_path / "exordos_universal_agent.conf"
    etc_path.write_text(_OLD_UA_CONFIG, encoding="utf-8")

    etc_path, data_path, run = _run(tmp_path)

    content = etc_path.read_text(encoding="utf-8")
    assert "    LBAgentCapabilityDriver,\n    BorderAgentCapabilityDriver,\n" in content
    assert "border_agent" in content
    # Stand-specific values and the following section survive the rewrite
    assert "orch_endpoint = http://10.20.0.2:11013" in content
    assert "[CoreDNSCertificateCapabilityDriver]" in content
    # The persisted copy is kept in sync
    assert data_path.read_text(encoding="utf-8") == content
    run.assert_called_once()
    assert "try-restart" in run.call_args.args[0]


def test_noop_on_current_config(tmp_path):
    etc_path = tmp_path / "exordos_universal_agent.conf"
    etc_path.write_text(_OLD_UA_CONFIG, encoding="utf-8")
    _run(tmp_path)
    content = etc_path.read_text(encoding="utf-8")

    etc_path, data_path, run = _run(tmp_path)

    assert etc_path.read_text(encoding="utf-8") == content
    run.assert_not_called()


def test_missing_config_is_skipped(tmp_path):
    etc_path, data_path, run = _run(tmp_path)

    assert not etc_path.exists()
    assert not data_path.exists()
    run.assert_not_called()
