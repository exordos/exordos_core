#    Copyright 2026 Genesis Corporation.
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

"""Which subnets the scheduler may put a node in."""

from unittest import mock

from exordos_core.compute import constants as nc
from exordos_core.network import service as net_service


def _subnet(driver="flat_bridge", placeable=True, next_server=None, uuid="net-1"):
    network = mock.MagicMock()
    network.uuid = uuid
    network.driver_spec = {"driver": driver}
    subnet = mock.MagicMock()
    subnet.network = network
    subnet.next_server = next_server
    subnet.placeable = placeable
    return subnet


def _node(pinned=None, set_pinned=None):
    node = mock.MagicMock()
    node.default_network = {nc.DEFAULT_NETWORK_KEY: pinned} if pinned else {}
    node.node_set = "set-1" if set_pinned else None
    node._set_pin = set_pinned
    return node


def _match(node, subnet):
    service = object.__new__(net_service.NetworkService)
    node_set = mock.MagicMock()
    node_set.default_network = (
        {nc.DEFAULT_NETWORK_KEY: node._set_pin} if node._set_pin else {}
    )
    objects = mock.MagicMock()
    objects.get_one_or_none.return_value = node_set
    with mock.patch.object(net_service.models.NodeSet, "objects", objects):
        return net_service.NetworkService._is_subnet_match(service, node, subnet)


def test_a_plain_flat_subnet_takes_nodes():
    assert _match(_node(), _subnet()) is True


def test_a_pool_subnet_never_takes_nodes():
    """An address pool hands out addresses for something else to claim.

    On a libvirt pool a subnet doubles as the name of the libvirt network a
    guest plugs into, and a pool has none — a node placed there comes up
    with nowhere to be, so the pool is not a placement target at all.
    """
    assert _match(_node(), _subnet(placeable=False)) is False


def test_a_pin_does_not_override_a_pool():
    """Honouring the pin would place the guest somewhere unable to carry it."""
    pool = _subnet(placeable=False, uuid="net-1")
    assert _match(_node(pinned="net-1"), pool) is False


def test_the_boot_subnet_still_never_takes_nodes():
    assert _match(_node(), _subnet(next_server="10.30.0.2")) is False


def test_an_unpinned_node_still_avoids_an_overlay():
    assert _match(_node(), _subnet(driver="ovs_evpn")) is False


def test_a_pinned_node_still_reaches_its_overlay():
    overlay = _subnet(driver="ovs_evpn", uuid="net-9")
    assert _match(_node(pinned="net-9"), overlay) is True


def test_the_set_pin_is_read_when_the_node_has_none():
    """The set is the authority; the node's copy is made from it.

    A set created and pinned in two steps generates its nodes in
    between, and a node placed by the default rule cannot be moved
    afterwards — so the set is consulted rather than only the copy.
    """
    overlay = _subnet(driver="ovs_evpn", uuid="net-9")
    assert _match(_node(set_pinned="net-9"), overlay) is True


def test_the_set_pin_also_keeps_a_node_off_other_networks():
    flat = _subnet(uuid="net-1")
    assert _match(_node(set_pinned="net-9"), flat) is False


def test_the_node_copy_wins_over_its_set():
    """Once placed, a node's own answer is the one that happened."""
    overlay = _subnet(driver="ovs_evpn", uuid="net-9")
    node = _node(pinned="net-9", set_pinned="net-other")
    assert _match(node, overlay) is True
