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

"""What a refresh from the data plane is allowed to forget about a port.

`port_info` mixes two kinds of facts: what libvirt can answer (mac, source)
and what only the control plane knows (address, port uuid, whether the port
is on an overlay). A refresh rebuilds the dict, and every control-plane key
it drops makes actual differ from target for ever: the machine is then
updated on every iteration, finds nothing to do, and logs "Unknown update
action" three times a second while `pool_machine` never leaves NEW.

That has now happened twice, first with `uuid` and then with `overlay`, so
these tests pin the whole preserved set rather than one spelling of it.
"""

from unittest import mock
import uuid as sys_uuid

import pytest

from exordos_core.compute.agents.universal.drivers import pool as pool_driver


TARGET_PORT_INFO = {
    "mac": "52:54:00:5e:ca:be",
    "ipv4": "10.20.0.22",
    "mask": "255.255.252.0",
    "uuid": "cc9d65e5-9bd6-47d9-864d-880694a10569",
    "source": "exordos-core-net",
    "overlay": False,
}


@pytest.fixture
def machine():
    """A meta machine holding what the control plane sent as target.

    Built, not `__new__`ed: without real initialisation the property
    descriptors share one slot and every assignment overwrites the last —
    the model then lies about itself and the test measures nothing.
    """
    return pool_driver.MetaMachine(
        uuid=sys_uuid.uuid4(),
        project_id=sys_uuid.uuid4(),
        port_info=dict(TARGET_PORT_INFO),
    )


def _dp_machine():
    dp = mock.MagicMock()
    dp.cores, dp.ram, dp.status, dp.boot = 2, 2048, "ACTIVE", "hd0"
    return dp


def _dp_port(mac=TARGET_PORT_INFO["mac"], source=TARGET_PORT_INFO["source"]):
    """A port as the data plane reports it: mac and source, nothing else."""
    port = mock.MagicMock()
    port.mac, port.source = mac, source
    return port


def _refresh(meta_machine, ports):
    # _from_dp_machine also touches image/_is_core_machine; the port half is
    # what these tests are about, so stop right after it.
    with mock.patch.object(
        type(meta_machine), "_is_core_machine", property(lambda self: True)
    ):
        meta_machine._from_dp_machine(_dp_machine(), ports)


def test_a_refresh_keeps_every_fact_the_data_plane_cannot_answer(machine):
    _refresh(machine, [_dp_port()])

    assert machine.port_info == TARGET_PORT_INFO


@pytest.mark.parametrize("key", pool_driver.MetaMachine.DP_OPAQUE_PORT_INFO)
def test_no_control_plane_key_is_dropped_by_a_refresh(machine, key):
    """The regression itself: a dropped key never converges."""
    _refresh(machine, [_dp_port()])

    assert key in machine.port_info, (
        f"{key} was dropped, so actual can never equal target and the "
        "machine is updated for ever"
    )
    assert machine.port_info[key] == TARGET_PORT_INFO[key]


def test_what_the_data_plane_does_answer_is_taken_from_it(machine):
    """mac and source are observed, not preserved."""
    _refresh(machine, [_dp_port(mac="52:54:00:00:00:01", source="br-int")])

    assert machine.port_info["mac"] == "52:54:00:00:00:01"
    assert machine.port_info["source"] == "br-int"


def test_a_key_the_target_never_carried_is_not_invented(machine):
    """An older installation has no `overlay`; a refresh must not add one.

    Inventing `overlay: None` here would be the same defect mirrored: the
    target has no such key, so actual would differ from it for ever.
    """
    machine.port_info.pop("overlay")

    _refresh(machine, [_dp_port()])

    assert "overlay" not in machine.port_info
