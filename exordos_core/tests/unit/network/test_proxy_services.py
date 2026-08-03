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

"""Doors from an overlay network onto services of the installation.

A door lets guests of a network reach one installation service on one port
through their hypervisor's proxy, which names the calling node. It is not a
route: the hypervisor fetches from the management network with the caller's
credentials, which is why every refusal below exists.
"""

import contextlib
from unittest import mock
import uuid as sys_uuid

import netaddr
import pytest

from exordos_core.common import exceptions as ex_exceptions
from exordos_core.compute import constants as nc
from exordos_core.network.driver import evpn
from exordos_core.user_api.network.dm import models as net_models

PROJECT = sys_uuid.uuid4()
OTHER_PROJECT = sys_uuid.uuid4()
NETWORK = sys_uuid.uuid4()
# Where the destination node's port sits, as opposed to where the door is.
PORT_NETWORK = sys_uuid.uuid4()
SUBNET = sys_uuid.uuid4()


def _door(port=11020, destination=None, network=NETWORK, project_id=PROJECT, **over):
    return net_models.ProxyService(
        name=over.pop("name", "paas"),
        project_id=project_id,
        network=network,
        port=port,
        destination=destination
        or net_models.ProxyDestinationNodeKind(node=over.pop("node", sys_uuid.uuid4())),
        **over,
    )


def _row(port, upstream, domain=None):
    """A stored door, as the compiler reads one."""
    row = mock.MagicMock()
    row.port = port
    row.domain = domain
    row.upstream.return_value = upstream
    return row


@contextlib.contextmanager
def _storage(
    network=None,
    node=None,
    siblings=(),
    port=None,
    floating=(),
    port_network=None,
):
    """Stand in for the tables validation and compilation consult.

    The door's network and the destination port's network differ; reachability
    is decided by the latter.
    """
    from exordos_core.compute.dm import models as compute_models

    networks = {str(NETWORK): network, str(PORT_NETWORK): port_network}

    def _network_by_uuid(filters=None, **kwargs):
        return networks.get(str(filters["uuid"].value))

    net_objects = mock.MagicMock()
    net_objects.get_one_or_none.side_effect = _network_by_uuid
    node_objects = mock.MagicMock()
    node_objects.get_one_or_none.return_value = node
    door_objects = mock.MagicMock()
    door_objects.get_all.return_value = list(siblings)
    subnet = mock.MagicMock()
    subnet.uuid = SUBNET
    subnet.network = PORT_NETWORK
    subnet_objects = mock.MagicMock()
    subnet_objects.get_one_or_none.return_value = subnet
    port_objects = mock.MagicMock()
    port_objects.get_all.return_value = [port] if port is not None else []
    address_objects = mock.MagicMock()
    address_objects.get_all.return_value = list(floating)
    with (
        mock.patch.object(compute_models.Network, "objects", net_objects),
        mock.patch.object(compute_models.Node, "objects", node_objects),
        mock.patch.object(compute_models.Subnet, "objects", subnet_objects),
        mock.patch.object(compute_models.Port, "objects", port_objects),
        mock.patch.object(net_models.Address, "objects", address_objects),
        mock.patch.object(net_models.ProxyService, "objects", door_objects),
    ):
        yield


def _overlay(project_id=PROJECT, access="private"):
    network = mock.MagicMock()
    network.uuid = NETWORK
    network.project_id = project_id
    network.driver_spec = {"driver": nc.OVERLAY_DRIVER}
    network.access = access
    return network


def _flat(project_id=PROJECT):
    network = mock.MagicMock()
    network.uuid = PORT_NETWORK
    network.project_id = project_id
    network.driver_spec = {"driver": "flat_bridge"}
    network.access = "private"
    return network


def _node(project_id=PROJECT, ipv4="10.20.0.5"):
    node = mock.MagicMock()
    node.uuid = sys_uuid.uuid4()
    node.project_id = project_id
    node.default_network = {"ipv4": ipv4} if ipv4 else {}
    return node


# --- what a door may name -------------------------------------------------


def test_a_door_may_only_lead_to_a_node_of_your_own():
    """The check that makes naming an object better than naming an address:
    an address is a string nobody can be asked about, and this is the same
    question every other citation in the network API answers."""
    with _storage(network=_overlay(), node=_node(project_id=OTHER_PROJECT)):
        with pytest.raises(ex_exceptions.ValidateException) as raised:
            _door()._validate()
    assert "another project" in str(raised.value)


def test_a_door_may_not_take_a_port_the_platform_answers_on():
    """A caller's upstream where every agent on the network sends its own
    reports, and no guest would have any way to notice."""
    for port in sorted(nc.PLATFORM_PROXY_PORTS):
        with _storage(network=_overlay(), node=_node()):
            with pytest.raises(ex_exceptions.ValidateException) as raised:
                _door(port=port)._validate()
        assert "platform's own" in str(raised.value)


def test_a_door_on_a_flat_network_is_refused():
    """There a guest reaches the service by routing to it, so a door is not
    a restriction lifted — it is a second path to the same place, with the
    hypervisor quietly in the middle of it."""
    flat = _overlay()
    flat.driver_spec = {"driver": "flat_bridge"}
    with _storage(network=flat, node=_node()):
        with pytest.raises(ex_exceptions.ValidateException) as raised:
            _door()._validate()
    assert "not an overlay" in str(raised.value)


def test_two_doors_may_not_share_a_port_on_one_network():
    """The compiler maps a port to exactly one upstream, so a second row on
    the same pair is one service answering where another one's clients are
    pointed — decided by whichever row a query returned first."""
    taken = mock.MagicMock()
    taken.uuid = sys_uuid.uuid4()
    with _storage(network=_overlay(), node=_node(), siblings=[taken]):
        with pytest.raises(ex_exceptions.ValidateException) as raised:
            _door()._validate()
    assert "already the door" in str(raised.value)


def test_a_door_onto_your_own_node_of_your_own_overlay_is_allowed():
    with _storage(network=_overlay(), node=_node()):
        _door()._validate()  # no raise


# --- where a door leads ---------------------------------------------------


def _leg(ipv4="10.20.0.9"):
    """The destination node's port on a network the installation routes."""
    port = mock.MagicMock()
    port.uuid = sys_uuid.uuid4()
    port.subnet = SUBNET
    port.ipv4 = netaddr.IPAddress(ipv4) if ipv4 else None
    port.index = 0
    return port


def test_the_upstream_of_a_node_door_is_looked_up_not_accepted():
    """The address is the platform's answer about the object, so a node that
    moves takes its door with it and a caller never states an address at
    all."""
    with _storage(network=_overlay(), node=_node(), port=_leg("10.20.0.9")):
        assert _door(port=11020).upstream() == "http://10.20.0.9:11020"


def test_the_upstream_is_the_nodes_port_not_what_the_node_says():
    """`default_network` is writable by the node's owner, so reading it
    would let a tenant point the hypervisor at any management address."""
    with _storage(
        network=_overlay(), node=_node(ipv4="10.0.0.1"), port=_leg("10.20.0.9")
    ):
        assert _door(port=11020).upstream() == "http://10.20.0.9:11020"


def test_a_door_onto_a_node_with_no_address_yet_is_not_a_door():
    """A node still being built has nothing behind it. Left out of the
    compiled set rather than written as an empty upstream — it arrives with
    the address, on the pass that follows."""
    with _storage(network=_overlay(), node=_node(), port=_leg(ipv4=None)):
        assert _door().upstream() is None
    with _storage(network=_overlay(), node=_node()):
        assert _door().upstream() is None


def test_an_address_door_needs_no_lookup():
    door = _door(
        destination=net_models.ProxyDestinationAddressKind(
            # The typed property wants the object, not the spelling.
            address=netaddr.IPAddress("10.20.0.7")
        ),
        port=8443,
    )
    with _storage(network=_overlay()):
        assert door.upstream() == "http://10.20.0.7:8443"


# --- what the compiler makes of them --------------------------------------


def test_the_doors_of_a_network_compile_to_the_proxys_services():
    rows = [
        _row(11021, "http://10.20.0.6:11021"),
        _row(11020, "http://10.20.0.5:11020", domain="paas.internal"),
        _row(11022, None),  # nothing behind it yet
    ]
    with mock.patch.object(evpn, "_proxy_service_rows", return_value=rows):
        assert evpn._proxy_services(NETWORK) == {
            "11020": "http://10.20.0.5:11020",
            "11021": "http://10.20.0.6:11021",
        }
        # Every door claims its port, including the one with nothing behind
        # it: a port not stolen sends the guest's packet out over the fabric
        # to an address the egress guard drops, and "connection refused" is
        # the better of the two — it also stops being wrong on its own.
        assert evpn._proxy_service_ports(NETWORK) == [11020, 11021, 11022]
        assert evpn._proxy_service_domains(NETWORK) == ["paas.internal"]


def test_an_unreadable_door_table_closes_no_doors():
    """Same rule as the published-address ledger: a query that failed says
    nothing about which doors exist, and reading it as "none" would close
    every one of them until something else happened to touch the network."""
    objects = mock.MagicMock()
    objects.get_all.side_effect = RuntimeError("no database here")
    with mock.patch.object(net_models.ProxyService, "objects", objects):
        assert evpn._proxy_services(NETWORK) == {}
        assert evpn._proxy_service_ports(NETWORK) == []


def test_the_compiled_order_does_not_move_with_the_query():
    """A target resource whose hash moved says something changed, and the
    hosts would reload their proxy on every pass."""
    rows = [_row(port, "http://10.20.0.5:%d" % port) for port in (11030, 11020, 11025)]
    with mock.patch.object(net_models.ProxyService, "objects") as objects:
        objects.get_all.return_value = rows
        assert [r.port for r in evpn._proxy_service_rows(NETWORK)] == [
            11020,
            11025,
            11030,
        ]


# --- who may open which kind of door --------------------------------------


def _controller():
    from exordos_core.user_api.network.api import controllers as net_controllers

    # Constructing a controller reads the IAM context off the thread, and a
    # unit test has no request to have put one there.
    with mock.patch("gcl_iam.api.controllers.contexts.get_context"):
        controller = net_controllers.ProxyServiceController(mock.Mock())
    controller._enforce = mock.Mock()
    return controller


def test_a_bare_address_needs_the_privileged_permission():
    """The node kind is answerable — is this yours? — and an address is not.
    The escape hatch belongs to whoever runs the installation."""
    controller = _controller()
    with _storage(network=_overlay()):
        controller._gate_privileged({"destination": {"kind": "address"}})
    controller._enforce.assert_called_once_with("custom_destination")


def test_a_door_on_a_published_network_needs_it_too():
    """The isolation boundary is the network, so a published one holds guests
    of other projects — and a door stands at the address and ports their
    agents treat as the platform's. Its owner would be handed everything
    they send there."""
    controller = _controller()
    with _storage(network=_overlay(access="public")):
        controller._gate_privileged({"network": NETWORK})
    controller._enforce.assert_called_once_with("custom_destination")


def test_a_node_door_on_your_own_private_network_needs_nothing_extra():
    """A project's owner opening a door from their own network onto their own
    node is the ordinary case, and it is what the plain create permission is
    for."""
    controller = _controller()
    with _storage(network=_overlay()):
        controller._gate_privileged(
            {"network": NETWORK, "destination": {"kind": "node"}}
        )
    controller._enforce.assert_not_called()


def test_an_update_that_says_nothing_about_the_network_still_checks_it():
    """ "Not mentioned" is not "not applicable": the row being edited is a
    door standing on a network, and which network that is has to be read
    from storage rather than from the request."""
    controller = _controller()
    stored = mock.MagicMock()
    stored.network = NETWORK
    with _storage(network=_overlay(access="public")):
        with mock.patch.object(net_models.ProxyService, "objects") as objects:
            objects.get_one_or_none.return_value = stored
            controller._gate_privileged({"domain": "x.internal"}, uuid=stored.uuid)
    controller._enforce.assert_called_once_with("custom_destination")


def test_an_update_of_an_address_door_needs_the_permission_it_was_opened_with():
    """Moving the port of a door onto a bare address is naming a new thing
    for the hypervisor to fetch, whether or not the body repeats the
    destination."""
    controller = _controller()
    stored = mock.MagicMock()
    stored.network = NETWORK
    stored.destination = mock.Mock(KIND="address")
    with _storage(network=_overlay()):
        with mock.patch.object(net_models.ProxyService, "objects") as objects:
            objects.get_one_or_none.return_value = stored
            controller._gate_privileged({"port": 8443}, uuid=stored.uuid)
    controller._enforce.assert_called_once_with("custom_destination")


# --- a door has to be the only path ---------------------------------------


def test_a_door_onto_a_guest_of_an_overlay_is_refused():
    """A door onto a guest of an overlay is refused.

    No hypervisor root namespace reaches a private overlay address (that would
    be inter-VRF routing). Judged by the leg the node has, not what it declared.
    """
    guest = _node(ipv4="10.100.0.7")
    port = mock.MagicMock()
    port.uuid = sys_uuid.uuid4()
    port.subnet = SUBNET
    with _storage(
        network=_overlay(),
        node=guest,
        port=port,
        port_network=_overlay(),
    ):
        with pytest.raises(ex_exceptions.ValidateException) as raised:
            _door()._validate()
    assert "guest of an overlay" in str(raised.value)


def test_a_door_beside_a_floating_address_is_refused():
    """Publishing an address opens it to the guests of every network on the
    host, above the guard's drops. That is a second path to the same service,
    and on it nobody is identified — so the door would look like it worked
    and prove nothing."""
    port = mock.MagicMock()
    port.uuid = sys_uuid.uuid4()
    port.subnet = SUBNET
    published = mock.MagicMock()
    published.address = "203.0.113.9"
    with _storage(
        network=_overlay(),
        node=_node(),
        port=port,
        floating=[published],
        port_network=_flat(),
    ):
        with pytest.raises(ex_exceptions.ValidateException) as raised:
            _door()._validate()
    assert "reach directly" in str(raised.value)


def test_a_node_on_the_flat_side_is_what_a_door_wants():
    """Where the platform's own services live: reachable from the root
    namespace, and dropped by the guard for every guest."""
    port = mock.MagicMock()
    port.uuid = sys_uuid.uuid4()
    port.subnet = SUBNET
    with _storage(
        network=_overlay(),
        node=_node(),
        port=port,
        floating=[],
        port_network=_flat(),
    ):
        _door()._validate()  # no raise


def test_a_second_path_that_arrives_later_shuts_the_door():
    """A second path added later shuts the door.

    A floating address on the destination does not touch the door's row, so
    the compiler re-checks rather than trusting the write-time check.
    """
    port = mock.MagicMock()
    port.uuid = sys_uuid.uuid4()
    port.subnet = SUBNET
    published = mock.MagicMock()
    published.address = "203.0.113.9"
    door = _door()

    with _storage(
        network=_overlay(), node=_node(), port=port, floating=[], port_network=_flat()
    ):
        assert door.upstream() is not None

    with _storage(
        network=_overlay(),
        node=_node(),
        port=port,
        floating=[published],
        port_network=_flat(),
    ):
        with mock.patch.object(net_models, "LOG"):
            assert door.upstream() is None
