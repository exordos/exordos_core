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

from exordos_core.bootstrap import defaults


def _spec():
    return {
        "stand": {
            # Naming a private network is what asks for an overlay at all,
            # and the reflector config exists to serve one.
            "private_network": {},
            "network": {"name": "flat", "cidr": "10.20.0.0/22"},
            "bootstraps": [
                {
                    "uuid": "5ded6d70-a989-4f88-8216-1c64274a1d6a",
                    "ports": [{"ip": "10.20.0.2", "mac": "52:54:00:82:4b:8f"}],
                }
            ],
        }
    }


def test_render_matches_live_reflector_config():
    # This is the exact [evpn] content proven to bring up the RR on a live
    # core (bgp_rr resource -> agent renders reflector gobgpd -> gobgpd up).
    assert defaults.render_evpn_rr_config(_spec()) == (
        "[evpn]\n"
        "rr_agent = 5ded6d70-a989-4f88-8216-1c64274a1d6a\n"
        "rr_addresses = 10.20.0.2\n"
        "rr_peer_prefixes = 10.20.0.0/22\n"
        "dns_forwarders = 10.20.0.2\n"
    )


def test_render_none_without_bootstraps():
    assert defaults.render_evpn_rr_config({"stand": {"network": {}}}) is None


def test_render_none_without_core_ip():
    spec = _spec()
    spec["stand"]["bootstraps"][0]["ports"] = [{"ip": None}]
    assert defaults.render_evpn_rr_config(spec) is None


def test_render_overrides_rr_address_and_peer_prefixes():
    # Multi-hypervisor realm: clients peer the RR through the control node's
    # flat address, and the RR accepts them from the flat network too.
    spec = _spec()
    spec["stand"]["evpn_rr_address"] = "10.20.0.23"
    spec["stand"]["evpn_peer_prefixes"] = ["10.20.0.0/22"]
    assert defaults.render_evpn_rr_config(spec) == (
        "[evpn]\n"
        "rr_agent = 5ded6d70-a989-4f88-8216-1c64274a1d6a\n"
        "rr_addresses = 10.20.0.23\n"
        "rr_peer_prefixes = 10.20.0.0/22\n"
        # dns_forwarders still points at the nested core's own resolver.
        "dns_forwarders = 10.20.0.2\n"
    )


def test_render_none_when_the_installation_asked_for_no_overlay():
    """The whole EVPN surface hangs off the stand naming a private network.

    Without it this file would configure a route reflector for a fabric
    nobody has -- and it is written on every bootstrap, each time restarting
    `ec-gservice`, on an installation that has been running without any
    overlay at all. Upgrading such an installation must change nothing.
    """
    spec = _spec()
    del spec["stand"]["private_network"]

    assert defaults.render_evpn_rr_config(spec) is None
