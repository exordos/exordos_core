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


def _run(tmp_path, sdn_agent=False, with_overlay=True):
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


def test_upgrades_old_persisted_config(tmp_path):
    etc_path = tmp_path / "exordos_universal_agent.conf"
    etc_path.write_text(_OLD_UA_CONFIG, encoding="utf-8")

    etc_path, data_path, run = _run(tmp_path)

    content = etc_path.read_text(encoding="utf-8")
    assert "    LBAgentCapabilityDriver,\n    BorderAgentCapabilityDriver,\n" in content
    assert "border_agent" in content
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


def test_a_node_with_its_own_sdn_agent_keeps_the_capability_released(tmp_path):
    """A hypervisor that wires guests into another installation's overlay has
    handed its evpn state to the dedicated SDN agent (`register-agent
    --write-config` strips the driver). Re-inserting it here would give the
    two agents one meta file again — silently, on the next boot."""
    etc_path = tmp_path / "exordos_universal_agent.conf"
    etc_path.write_text(_OLD_UA_CONFIG, encoding="utf-8")

    etc_path, _data_path, _run_mock = _run(tmp_path, sdn_agent=True)

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

    etc_path, _data_path, _run_mock = _run(tmp_path, with_overlay=False)

    content = etc_path.read_text(encoding="utf-8")
    assert "EvpnAgentCapabilityDriver" not in content
    for capability in ("evpn_port", "evpn_host", "evpn_nf", "bgp_rr"):
        assert capability not in content
    # ... and everything this function maintained before the overlay existed
    # still lands, because that part is not conditional on anything.
    assert "BorderAgentCapabilityDriver" in content
    assert "border_agent" in content


def test_the_shipped_agent_config_offers_no_overlay_capability():
    """The image's own config is the one a freshly flashed node starts with,
    and it never passes through `_ensure_ua_config_current`'s gate.

    Shipping the evpn driver and capabilities in it made every core built
    from this image advertise an overlay it may have no part in -- measured:
    a released installation upgraded to such an image came up with the
    agent's guest store missing, its pool reporting zero capacity, and every
    machine in ERROR, while the same upgrade between released versions was
    fine. The overlay entries are added at bootstrap, by the code above,
    when the installation asked for one.
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
