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

"""Where the route reflector is, when nobody wrote it down.

The bootstrap's `[evpn]` drop-in is read only at `ec-gservice` startup, so a
network created on a running installation derives the same facts from the
database.
"""

import contextlib
from unittest import mock
import uuid as sys_uuid

import pytest

from exordos_core.common import constants as c
from exordos_core.compute import constants as nc
from exordos_core.network.driver import evpn


@pytest.fixture
def driver():
    driver = evpn.OvsEvpnNetworkDriver.__new__(evpn.OvsEvpnNetworkDriver)
    driver._derived_rr_memo = None
    return driver


def _installation(node_set, port_ipv4, network_driver="flat_bridge"):
    """One node with one port on one subnet of one network."""
    node = mock.MagicMock(uuid=sys_uuid.uuid4(), node_set=node_set)
    subnet = mock.MagicMock(uuid=sys_uuid.uuid4(), cidr="10.20.0.0/22")
    subnet.network = sys_uuid.uuid4()
    port = mock.MagicMock(ipv4=port_ipv4, subnet=subnet.uuid)
    network = mock.MagicMock(driver_spec={"driver": network_driver})
    return node, port, subnet, network


@contextlib.contextmanager
def _wire(node, port, subnet, network, core_ip="10.20.0.2"):
    """Both places the driver reads: the compute rows and the address the
    bootstrap recorded. `core_ip=None` is an installation bootstrapped
    before it was recorded."""
    with (
        mock.patch.object(evpn, "models") as models,
        mock.patch.object(evpn, "vs_models") as vs,
    ):
        models.Node.objects.get_one_or_none.return_value = node
        models.Port.objects.get_all.return_value = [port] if port else []
        models.Subnet.objects.get_one_or_none.return_value = subnet
        models.Network.objects.get_one_or_none.return_value = network
        vs.Value.objects.get_one_or_none.return_value = (
            mock.MagicMock(value=core_ip) if core_ip else None
        )
        yield models


def test_the_reflector_is_the_core_node_and_its_underlay_address(driver):
    node, port, subnet, network = _installation(c.CORE_SET_UUID, "10.20.0.2")
    with _wire(node, port, subnet, network) as models:
        assert driver._derived_rr() == (node.uuid, "10.20.0.2", "10.20.0.0/22")
        # Asked for by the set it belongs to, which is a fact, rather than by
        # the name it happens to carry.
        assert (
            models.Node.objects.get_one_or_none.call_args.kwargs["filters"][
                "node_set"
            ].value
            == c.CORE_SET_UUID
        )


def test_the_recorded_address_wins_over_the_port_of_the_node(driver):
    """The recorded core address wins over the node's port address.

    On a bootstrapped installation they differ: the port has IPAM's (`...20`),
    the core answers on the stand spec's (`...2`).
    """
    node, port, subnet, network = _installation(c.CORE_SET_UUID, "10.20.0.20")
    with _wire(node, port, subnet, network, core_ip="10.20.0.2"):
        assert driver._derived_rr() == (node.uuid, "10.20.0.2", "10.20.0.0/22")


def test_without_a_recorded_address_the_port_is_the_answer(driver):
    """An installation bootstrapped before it was written down. The subnet is
    the port's either way -- that is the underlay, and the port says it."""
    node, port, subnet, network = _installation(c.CORE_SET_UUID, "10.20.0.20")
    with _wire(node, port, subnet, network, core_ip=None):
        assert driver._derived_rr() == (node.uuid, "10.20.0.20", "10.20.0.0/22")


def test_an_overlay_leg_is_not_the_underlay_the_reflector_peers_over(driver):
    node, port, subnet, network = _installation(
        c.CORE_SET_UUID, "10.100.0.1", network_driver=nc.OVERLAY_DRIVER
    )
    with _wire(node, port, subnet, network):
        assert driver._derived_rr() is None


def test_an_installation_that_cannot_answer_yet_writes_nothing(driver):
    """During a first bootstrap the core node has no address. Silence is what
    an unconfigured installation did before, and is the safe answer."""
    with _wire(None, None, None, None):
        assert driver._derived_rr() is None


def test_a_failure_to_ask_is_not_an_answer(driver):
    with _wire(None, None, None, None) as models:
        models.Node.objects.get_one_or_none.side_effect = RuntimeError("no database")
        assert driver._derived_rr() is None


def test_only_a_real_answer_is_kept(driver):
    """A driver built before the node had an address must ask again."""
    with _wire(None, None, None, None):
        assert driver._derived_rr() is None
    node, port, subnet, network = _installation(c.CORE_SET_UUID, "10.20.0.2")
    with _wire(node, port, subnet, network):
        assert driver._derived_rr() is not None


def test_the_configured_reflector_still_wins(driver):
    """A multi-hypervisor realm reflects on an address its hypervisors reach
    from outside the nested core's network — the one case no local fact can
    produce."""
    configured = str(sys_uuid.uuid4())
    with (
        mock.patch.object(evpn.CONF, "evpn") as conf,
        mock.patch.object(driver, "_derived_rr") as derived,
        mock.patch.object(evpn.evpn_models, "BgpRr") as bgp_rr,
        mock.patch.object(driver, "_get_resource", side_effect=Exception("boom")),
    ):
        conf.rr_agent = configured
        conf.rr_peer_prefixes = ["192.168.100.0/24"]
        conf.as_number = 65001
        derived.return_value = (sys_uuid.uuid4(), "10.20.0.2", "10.20.0.0/22")
        try:
            driver._ensure_rr()
        except Exception:
            pass

    assert bgp_rr.call_args.kwargs["uuid"] == sys_uuid.UUID(configured)
    assert bgp_rr.call_args.kwargs["peer_prefixes"] == ["192.168.100.0/24"]


def test_the_addresses_fall_back_to_the_derived_one(driver):
    with mock.patch.object(driver, "_derived_rr") as derived:
        derived.return_value = (sys_uuid.uuid4(), "10.20.0.2", "10.20.0.0/22")
        assert driver._configured_or_core([]) == ["10.20.0.2"]
        assert driver._configured_or_core(["10.9.9.9"]) == ["10.9.9.9"]


class TestTheAgentIsOfferedTheOverlay:
    """The other half: a capability is an offer, and the offer is in the
    agent's config, which is read when the agent starts."""

    def setup_method(self):
        evpn.OvsEvpnNetworkDriver._agent_wiring_attempted = False

    def teardown_method(self):
        evpn.OvsEvpnNetworkDriver._agent_wiring_attempted = False

    def test_the_running_driver_is_itself_the_answer_to_wanting_an_overlay(
        self, driver
    ):
        from exordos_core.bootstrap import defaults as core_bootstrap

        with mock.patch.object(core_bootstrap, "_ensure_ua_config_current") as ensure:
            driver._ensure_agent_offers_overlay()

        ensure.assert_called_once_with(with_overlay=True)

    def test_it_is_tried_once_per_process_not_once_per_loop(self, driver):
        """The config is a file the loop does not own. An operator who took the
        capabilities out on purpose must not get them back, and the agent
        restarted, on every loop for ever."""
        from exordos_core.bootstrap import defaults as core_bootstrap

        with mock.patch.object(core_bootstrap, "_ensure_ua_config_current") as ensure:
            for _ in range(5):
                driver._ensure_agent_offers_overlay()

        assert ensure.call_count == 1

    def test_a_failure_leaves_the_loop_running(self, driver):
        from exordos_core.bootstrap import defaults as core_bootstrap

        with mock.patch.object(
            core_bootstrap,
            "_ensure_ua_config_current",
            side_effect=OSError("read-only /etc"),
        ):
            driver._ensure_agent_offers_overlay()  # must not raise
