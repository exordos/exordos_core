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

"""Bringing a node's ports in line with what it declared.

Three states, the same three Nova spells `auto` / a list / `none`:
`interfaces` unset means the node said nothing, a list is the whole truth
about its legs, and an empty list means it wants none.
"""

from unittest import mock
import uuid as sys_uuid

import pytest

from gcl_sdk.infra.dm import models as sdk_models

from exordos_core.compute import constants as nc
from exordos_core.compute.dm import models as compute_models
from exordos_core.network import service as net_service


PROJECT = sys_uuid.UUID("11111111-1111-1111-1111-111111111111")
SUBNET_A = sys_uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SUBNET_B = sys_uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _node(interfaces=None, default_network=None):
    node = mock.MagicMock()
    node.uuid = sys_uuid.uuid4()
    node.project_id = PROJECT
    node.interfaces = interfaces
    node.default_network = default_network or {}
    return node


def _leg(**kwargs):
    return compute_models.NodeInterfaceOnSubnetKind(**kwargs)


def _port(index=0, provenance=nc.PortProvenance.GENERATED.value, subnet=SUBNET_A):
    port = mock.MagicMock()
    port.uuid = sys_uuid.uuid4()
    port.index = index
    port.provenance = provenance
    port.subnet = subnet
    port.ipv4 = None
    port.config = compute_models.PortSimpleKind()
    port.port_security = True
    return port


def _subnet(uuid):
    subnet = mock.MagicMock()
    subnet.uuid = uuid
    return subnet


class _Harness:
    """Runs the reconciler with port construction stubbed out.

    What is under test here is which legs are built, adopted and removed —
    building one is `_build_port`'s job and is exercised where a database
    is available.
    """

    def __init__(self, subnets=(SUBNET_A, SUBNET_B)):
        self.service = object.__new__(net_service.NetworkService)
        self.subnet_map = {_subnet(uuid): [] for uuid in subnets}
        self.built = []

    def run(self, node, existing=()):
        def _build(**kwargs):
            self.built.append(kwargs)
            return mock.MagicMock()

        def _resolve(leg, _map, strict=True):
            return _subnet(leg.subnet if leg.subnet is not None else leg.network)

        with (
            mock.patch.object(self.service, "_build_port", side_effect=_build),
            mock.patch.object(
                self.service,
                "_auto_subnet",
                return_value=next(iter(self.subnet_map)),
            ),
            mock.patch.object(self.service, "_resolve_subnet", side_effect=_resolve),
        ):
            self.service._reconcile_interfaces(
                node, list(existing), mock.MagicMock(), self.subnet_map
            )
        return self.built


# --- saying nothing -------------------------------------------------------


def test_an_undeclared_node_still_gets_exactly_one_port():
    """The upgrade case. Every node predating `interfaces` is in it."""
    harness = _Harness()
    built = harness.run(_node(interfaces=None))
    assert len(built) == 1
    assert built[0]["index"] == 0
    assert built[0]["config"] is None


def test_an_undeclared_node_that_already_has_a_port_is_left_alone():
    """It was placed once and cannot be moved; saying nothing changes nothing."""
    harness = _Harness()
    assert harness.run(_node(interfaces=None), existing=[_port()]) == []


# --- the released way of asking, kept for one more release ----------------


def test_a_legacy_network_pin_is_still_honoured():
    """Sealing `default_network` would be a 400 on the next reconcile.

    An installation upgrades without touching its manifests, so what those
    manifests say has to keep meaning what it meant.
    """
    harness = _Harness()
    built = harness.run(_node(default_network={"network": str(SUBNET_B)}))
    assert len(built) == 1
    assert built[0]["subnet"].uuid == SUBNET_B


def test_a_legacy_fixed_address_is_still_honoured():
    """`target_ipv4` shipped: it is read by the released placement path."""
    harness = _Harness()
    built = harness.run(_node(default_network={"target_ipv4": "10.0.0.50"}))
    assert str(built[0]["target_ip"]) == "10.0.0.50"


def test_a_declaration_wins_over_the_legacy_keys():
    """A manifest that has moved is unaffected by any of the above."""
    harness = _Harness()
    built = harness.run(
        _node(
            interfaces=[_leg(subnet=SUBNET_A)],
            default_network={"network": str(SUBNET_B)},
        )
    )
    assert [b["subnet"].uuid for b in built] == [SUBNET_A]


def test_the_answer_written_back_is_not_mistaken_for_a_request():
    """`default_network` is overwritten with the port once one exists.

    Those keys — `subnet`, `port`, `ipv4`, `mask`, `mac` — are the answer,
    and reading them back as a pin is how a node would place itself from
    its own reflection.
    """
    harness = _Harness()
    node = _node(
        default_network={
            "subnet": str(SUBNET_B),
            "port": str(sys_uuid.uuid4()),
            "ipv4": "10.0.0.7",
            "mask": "255.255.255.0",
            "mac": "52:54:00:aa:bb:cc",
        }
    )
    built = harness.run(node)
    assert built[0]["subnet"].uuid == next(iter(harness.subnet_map)).uuid
    assert built[0]["target_ip"] is None


# --- declaring legs -------------------------------------------------------


def test_declared_legs_are_built_in_declaration_order():
    harness = _Harness()
    built = harness.run(
        _node(interfaces=[_leg(subnet=SUBNET_A), _leg(subnet=SUBNET_B)])
    )
    assert [b["index"] for b in built] == [0, 1]
    assert [b["subnet"].uuid for b in built] == [SUBNET_A, SUBNET_B]


def test_an_explicit_index_decides_which_leg_is_primary():
    """Index 0 is the primary interface, and it is stated, not positional."""
    harness = _Harness()
    built = harness.run(
        _node(
            interfaces=[
                _leg(subnet=SUBNET_A, index=1),
                _leg(subnet=SUBNET_B, index=0),
            ]
        )
    )
    assert [(b["index"], b["subnet"].uuid) for b in built] == [
        (0, SUBNET_B),
        (1, SUBNET_A),
    ]


def test_a_node_that_wants_no_networking_gets_none():
    """An empty list is a statement, and a different one from saying nothing."""
    assert _Harness().run(_node(interfaces=[])) == []


def test_a_leg_already_built_is_not_built_again():
    harness = _Harness()
    built = harness.run(
        _node(interfaces=[_leg(subnet=SUBNET_A), _leg(subnet=SUBNET_B)]),
        existing=[_port(index=0)],
    )
    assert [b["index"] for b in built] == [1]


# --- convergence, and its limit -------------------------------------------


def test_a_leg_removed_from_the_declaration_is_taken_away():
    """The declaration is the whole truth about the legs it owns."""
    harness = _Harness()
    stale = _port(index=1)
    harness.run(_node(interfaces=[_leg(subnet=SUBNET_A)]), existing=[_port(), stale])
    stale.delete.assert_called_once()


def test_a_port_somebody_else_made_is_never_taken_away():
    """What keeps a converging declaration from fighting /v1/network/ports.

    An operator's leg is not in the declaration and never was, so a
    declaration that does not mention it is not a request to remove it.
    """
    harness = _Harness()
    theirs = _port(index=1, provenance=nc.PortProvenance.USER.value)
    harness.run(_node(interfaces=[_leg(subnet=SUBNET_A)]), existing=[_port(), theirs])
    theirs.delete.assert_not_called()


# --- adopting -------------------------------------------------------------


def test_naming_an_existing_port_attaches_it_without_rewriting_it():
    service = object.__new__(net_service.NetworkService)
    node = _node()
    existing = _port(index=0, provenance=nc.PortProvenance.USER.value)
    existing.project_id = PROJECT
    existing.node = None
    objects = mock.MagicMock()
    objects.get_one_or_none.return_value = existing
    with mock.patch.object(net_service.net_models.Port, "objects", objects):
        service._adopt_port(
            node,
            compute_models.NodeInterfaceOnPortKind(port=existing.uuid),
            index=1,
        )
    assert existing.node == node.uuid
    assert existing.index == 1
    # Its provenance is untouched: the node named it, it did not make it.
    assert existing.provenance == nc.PortProvenance.USER.value
    existing.update.assert_called_once()


def test_naming_a_port_that_is_already_another_nodes_leg_is_refused():
    service = object.__new__(net_service.NetworkService)
    taken = _port()
    taken.project_id = PROJECT
    taken.node = mock.MagicMock()
    taken.node.uuid = sys_uuid.uuid4()
    objects = mock.MagicMock()
    objects.get_one_or_none.return_value = taken
    with mock.patch.object(net_service.net_models.Port, "objects", objects):
        with pytest.raises(net_service.PlacementError, match="already a leg"):
            service._adopt_port(
                _node(),
                compute_models.NodeInterfaceOnPortKind(port=taken.uuid),
                index=0,
            )


def test_naming_a_port_of_another_project_is_refused():
    service = object.__new__(net_service.NetworkService)
    theirs = _port()
    theirs.project_id = sys_uuid.uuid4()
    theirs.node = None
    objects = mock.MagicMock()
    objects.get_one_or_none.return_value = theirs
    with mock.patch.object(net_service.net_models.Port, "objects", objects):
        with pytest.raises(net_service.PlacementError, match="another project"):
            service._adopt_port(
                _node(),
                compute_models.NodeInterfaceOnPortKind(port=theirs.uuid),
                index=0,
            )


# --- the shapes a node is refused on the way in ---------------------------


def _stored_node(interfaces):
    """A node model, real enough to be asked to store itself."""
    return compute_models.Node(
        uuid=sys_uuid.uuid4(),
        name="node",
        project_id=PROJECT,
        cores=2,
        ram=1024,
        disk_spec=sdk_models.RootDiskSpec(size=10, image="http://img"),
        interfaces=interfaces,
    )


@pytest.mark.parametrize(
    "interfaces, message",
    (
        ([_leg(subnet=SUBNET_A, network=SUBNET_B)], "exactly one"),
        ([_leg()], "exactly one"),
        ([_leg(subnet=SUBNET_A), _leg(subnet=SUBNET_B, index=0)], "claim index 0"),
    ),
)
def test_a_malformed_declaration_is_refused_before_it_is_stored(interfaces, message):
    """A malformed declaration is refused on create, not only on update.

    A node may never be updated; what gets through is settled by row order,
    which is the race the rule exists to refuse.
    """
    node = _stored_node(interfaces)
    with mock.patch(
        "restalchemy.storage.sql.orm.SQLStorableWithJSONFieldsMixin.insert"
    ) as stored:
        with pytest.raises(Exception, match=message):
            node.insert()
    stored.assert_not_called()


def test_a_well_formed_declaration_gets_past_the_check():
    node = _stored_node([_leg(subnet=SUBNET_A), _leg(network=SUBNET_B)])

    node._validate_interfaces()


def test_a_set_nodes_legs_belong_to_the_sets_owner():
    """A set's nodes live in the installation's set project, and a leg built
    there could name none of the owner's groups, subnet or address."""
    owner = sys_uuid.uuid4()
    node = _node()
    node.node_set = sys_uuid.uuid4()
    node.project_id = nc.NODE_SET_PROJECT
    with mock.patch.object(compute_models.NodeSet, "objects") as objects:
        objects.get_one_or_none.return_value = mock.Mock(project_id=owner)
        assert net_service.NetworkService._leg_project(node) == owner

    plain = _node()
    assert net_service.NetworkService._leg_project(plain) == plain.project_id


def test_an_edited_leg_is_brought_in_line_in_place():
    """Groups and port security are the port's own settings: edited in the
    declaration, they are edited on the port, not rebuilt."""
    harness = _Harness()
    port = _port(index=0)
    group = sys_uuid.uuid4()
    leg = _leg(
        subnet=SUBNET_A,
        port_security=False,
        config=compute_models.PortSimpleKind(security_groups=[group]),
    )
    built = harness.run(_node(interfaces=[leg]), existing=[port])
    assert built == []
    port.delete.assert_not_called()
    port.update.assert_called_once()
    assert port.port_security is False
    assert port.config.security_groups == [group]


def test_a_leg_moved_to_another_subnet_is_rebuilt():
    harness = _Harness()
    port = _port(index=0, subnet=SUBNET_A)
    built = harness.run(_node(interfaces=[_leg(subnet=SUBNET_B)]), existing=[port])
    port.delete.assert_called_once()
    assert [b["subnet"].uuid for b in built] == [SUBNET_B]


def test_an_unchanged_leg_is_not_touched():
    harness = _Harness()
    port = _port(index=0)
    harness.run(_node(interfaces=[_leg(subnet=SUBNET_A)]), existing=[port])
    port.update.assert_not_called()
    port.delete.assert_not_called()


def test_a_leg_named_by_network_holds_while_its_subnet_is_on_it():
    """A second placeable subnet on the network makes the name ambiguous for
    a new leg, not for one already built."""
    network = sys_uuid.uuid4()
    harness = _Harness()
    for subnet in harness.subnet_map:
        subnet.network = network
    port = _port(index=0, subnet=SUBNET_B)
    built = harness.run(_node(interfaces=[_leg(network=network)]), existing=[port])
    assert built == []
    port.delete.assert_not_called()


def test_a_leg_read_back_with_its_subnet_row_holds():
    """Loaded from the database, a port's subnet is the row, not its uuid:
    a leg compared by the row's text would be rebuilt on every pass."""
    network = sys_uuid.uuid4()
    harness = _Harness()
    for subnet in harness.subnet_map:
        subnet.network = network
    by_subnet = _port(index=0, subnet=_subnet(SUBNET_A))
    by_network = _port(index=1, subnet=_subnet(SUBNET_B))
    built = harness.run(
        _node(interfaces=[_leg(subnet=SUBNET_A), _leg(network=network)]),
        existing=[by_subnet, by_network],
    )
    assert built == []
    by_subnet.delete.assert_not_called()
    by_network.delete.assert_not_called()
