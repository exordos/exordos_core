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

"""Which subnet a leg lands on — declared, or left to the legacy rule."""

from unittest import mock
import uuid as sys_uuid

import pytest

from exordos_core.compute.dm import models as compute_models
from exordos_core.network import service as net_service


FLAT_NET = sys_uuid.UUID("11111111-1111-1111-1111-111111111111")
OVERLAY_NET = sys_uuid.UUID("99999999-9999-9999-9999-999999999999")


def _subnet(
    driver="flat_bridge",
    placeable=True,
    next_server=None,
    network=FLAT_NET,
    uuid=None,
):
    net = mock.MagicMock()
    net.uuid = network
    net.driver_spec = {"driver": driver}
    subnet = mock.MagicMock()
    subnet.uuid = uuid or sys_uuid.uuid4()
    subnet.network = net
    subnet.next_server = next_server
    subnet.placeable = placeable
    return subnet


def _map(*subnets):
    return {subnet: [] for subnet in subnets}


def _service():
    return object.__new__(net_service.NetworkService)


def _leg(subnet=None, network=None):
    return compute_models.NodeInterfaceOnSubnetKind(subnet=subnet, network=network)


# The legacy rule: what a node that declared nothing gets.


def test_an_undeclared_node_lands_on_a_plain_flat_subnet():
    flat = _subnet()
    assert _service()._auto_subnet(_map(flat)) is flat


def test_an_undeclared_node_skips_a_pool():
    """An address pool hands out addresses for something else to claim.

    On a libvirt pool a subnet doubles as the name of the libvirt network a
    guest plugs into, and a pool has none — a node placed there comes up
    with nowhere to be, so the pool is not a placement target at all.
    """
    flat = _subnet()
    assert _service()._auto_subnet(_map(_subnet(placeable=False), flat)) is flat


def test_an_undeclared_node_skips_the_boot_subnet():
    flat = _subnet()
    assert _service()._auto_subnet(_map(_subnet(next_server="10.30.0.2"), flat)) is flat


def test_an_undeclared_node_never_lands_on_an_overlay():
    """Overlays stay opt-in, which is what kept existing nodes where they were."""
    with pytest.raises(net_service.PlacementError):
        _service()._auto_subnet(_map(_subnet(driver="ovs_evpn")))


# A declared leg.


def test_a_named_subnet_is_taken_as_named():
    overlay = _subnet(driver="ovs_evpn", network=OVERLAY_NET)
    resolved = _service()._resolve_subnet(_leg(subnet=overlay.uuid), _map(overlay))
    assert resolved is overlay


def test_a_subnet_that_does_not_exist_is_refused():
    with pytest.raises(net_service.PlacementError, match="does not exist"):
        _service()._resolve_subnet(_leg(subnet=sys_uuid.uuid4()), _map(_subnet()))


def test_a_network_with_one_placeable_subnet_resolves_to_it():
    """The sugar case, and the only one a network may stand for."""
    only = _subnet(network=OVERLAY_NET)
    assert _service()._resolve_subnet(_leg(network=OVERLAY_NET), _map(only)) is only


def test_a_network_ignores_its_pools_when_resolving():
    pool = _subnet(network=OVERLAY_NET, placeable=False)
    workload = _subnet(network=OVERLAY_NET)
    resolved = _service()._resolve_subnet(
        _leg(network=OVERLAY_NET), _map(pool, workload)
    )
    assert resolved is workload


def test_a_network_with_two_placeable_subnets_is_refused_with_both_named():
    """The ambiguity the old pin settled by row order, made loud.

    Picking one silently is what made the same manifest place nodes
    differently on two installations, so the answer is the caller's to give.
    """
    first = _subnet(network=OVERLAY_NET)
    second = _subnet(network=OVERLAY_NET)
    with pytest.raises(net_service.PlacementError) as err:
        _service()._resolve_subnet(_leg(network=OVERLAY_NET), _map(first, second))
    assert str(first.uuid) in str(err.value)
    assert str(second.uuid) in str(err.value)


def test_a_network_that_can_carry_nothing_is_refused():
    pool = _subnet(network=OVERLAY_NET, placeable=False)
    with pytest.raises(net_service.PlacementError, match="no subnet"):
        _service()._resolve_subnet(_leg(network=OVERLAY_NET), _map(pool))
