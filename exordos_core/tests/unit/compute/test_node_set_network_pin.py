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

`NodeSet.default_network` has been settable since the field existed and
reached nothing — the nodes were generated without it. These hold both
halves of fixing that: the pin now travels, and a set that pins nothing
generates exactly what it generated before, because installations exist
that were built without one and are upgraded rather than rebuilt.
"""

import uuid as sys_uuid

from exordos_core.compute import constants as nc
from exordos_core.compute.node_set.dm import models as node_set_models
from gcl_sdk.infra.dm import models as sdk_models


PROJECT = sys_uuid.UUID("11111111-1111-1111-1111-111111111111")
NETWORK = sys_uuid.UUID("22222222-2222-2222-2222-222222222222")


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


def test_a_set_that_pins_nothing_generates_what_it_always_did():
    """The upgrade case, and the one that must not move.

    A set built before the pin existed carries no network, and its nodes
    have to come out with the empty `default_network` they came out with
    before — the network service places an unpinned node exactly as it
    used to, and never on an overlay.
    """
    nodes = _node_set().gen_nodes(PROJECT)
    assert len(nodes) == 2
    for node in nodes:
        assert node.default_network == {}


def test_the_pin_reaches_the_nodes():
    nodes = _node_set(default_network={nc.DEFAULT_NETWORK_KEY: str(NETWORK)}).gen_nodes(
        PROJECT
    )
    assert len(nodes) == 2
    for node in nodes:
        assert node.default_network == {nc.DEFAULT_NETWORK_KEY: str(NETWORK)}


def test_only_the_pin_travels():
    """The rest of `default_network` describes a port, and a set has none.

    A node's own address, MAC and port are written when the network
    service places it. Carrying a set's copy of those would hand every
    node of the set one another's port.
    """
    nodes = _node_set(
        default_network={
            nc.DEFAULT_NETWORK_KEY: str(NETWORK),
            "ipv4": "10.100.0.10",
            "port": str(sys_uuid.uuid4()),
            "mac": "52:54:00:aa:bb:cc",
        }
    ).gen_nodes(PROJECT)
    for node in nodes:
        assert node.default_network == {nc.DEFAULT_NETWORK_KEY: str(NETWORK)}
