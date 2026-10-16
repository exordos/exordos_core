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

"""What a node set says about where its nodes belong.

The template travels to the nodes, and a set that declares nothing
generates exactly what it did before, as upgraded installations need.
"""

import uuid as sys_uuid

import netaddr
import pytest

from exordos_core.common import exceptions as ex_exceptions
from exordos_core.compute import constants as nc
from exordos_core.compute.dm import models as compute_models
from exordos_core.compute.node_set.dm import models as node_set_models
from gcl_sdk.infra.dm import models as sdk_models


PROJECT = sys_uuid.UUID("11111111-1111-1111-1111-111111111111")
SUBNET = sys_uuid.UUID("22222222-2222-2222-2222-222222222222")


def _node_set(**kwargs) -> node_set_models.NodeSet:
    return node_set_models.NodeSet(
        uuid=sys_uuid.uuid4(),
        name="set",
        project_id=PROJECT,
        cores=2,
        ram=1024,
        replicas=2,
        status=nc.NodeStatus.NEW.value,
        disk_spec=sdk_models.SetRootDiskSpec(size=10, image="http://img"),
        **kwargs,
    )


def _leg(**kwargs) -> compute_models.NodeInterfaceOnSubnetKind:
    return compute_models.NodeInterfaceOnSubnetKind(**kwargs)


def test_a_set_that_declares_nothing_generates_what_it_always_did():
    """A set built before `interfaces` existed yields nodes declaring nothing.

    Such a node is placed as before, never on an overlay.
    """
    nodes = _node_set().gen_nodes(PROJECT)
    assert len(nodes) == 2
    for node in nodes:
        assert node.interfaces is None


def test_the_template_reaches_the_nodes():
    nodes = _node_set(interfaces=[_leg(subnet=SUBNET)]).gen_nodes(PROJECT)
    assert len(nodes) == 2
    for node in nodes:
        assert [i.subnet for i in node.interfaces] == [SUBNET]


def test_a_set_that_wants_no_networking_hands_that_down_too():
    """An empty list is a statement, and a different one from saying nothing."""
    nodes = _node_set(interfaces=[]).gen_nodes(PROJECT)
    for node in nodes:
        assert node.interfaces == []


def test_a_set_cannot_hand_every_replica_the_same_address():
    """An address is the node's, not the set's: replicas would race for it."""
    node_set = _node_set(
        interfaces=[_leg(subnet=SUBNET, address=netaddr.IPAddress("10.100.0.10"))]
    )
    with pytest.raises(ex_exceptions.ValidateException, match="address"):
        node_set._validate_interfaces()


NETWORK = sys_uuid.UUID("33333333-3333-3333-3333-333333333333")


def test_a_set_is_held_to_the_shape_its_nodes_are():
    """A set is validated like its nodes' legs.

    Refused only at the node, a bad leg reaches every replica and a reconcile
    loop that can only log it.
    """
    both = _node_set(interfaces=[_leg(subnet=SUBNET, network=NETWORK)])
    with pytest.raises(ex_exceptions.ValidateException, match="exactly one"):
        both._validate_interfaces()

    neither = _node_set(interfaces=[_leg()])
    with pytest.raises(ex_exceptions.ValidateException, match="exactly one"):
        neither._validate_interfaces()

    collided = _node_set(
        interfaces=[_leg(subnet=SUBNET, index=0), _leg(network=NETWORK, index=0)]
    )
    with pytest.raises(ex_exceptions.ValidateException, match="claim index 0"):
        collided._validate_interfaces()


def test_the_declaration_is_among_what_a_node_carries():
    """A node carries its set's `interfaces` through the target resource.

    A target resource keeps only its target fields; without this one the node
    lost the declaration and was placed on the flat network.
    """
    assert "interfaces" in compute_models.Node.get_resource_target_fields(
        compute_models.Node
    )
