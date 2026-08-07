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

"""Which boot mode an update leaves a machine in (compute/builders/pool)."""

from unittest import mock
import uuid as sys_uuid

import pytest

from exordos_core.compute import constants as nc
from exordos_core.compute.builders import pool as pool_builder

IMAGE = "http://repo/exordos-base.raw.zst"


@pytest.fixture
def builder():
    return pool_builder.PoolBuilderService.__new__(pool_builder.PoolBuilderService)


def _machine(name="worker-a"):
    machine = mock.MagicMock()
    machine.uuid = sys_uuid.uuid4()
    machine.name = name
    machine.node.uuid = sys_uuid.uuid4()
    return machine


def _guest(boot, image=IMAGE):
    guest = mock.MagicMock()
    guest.boot = boot
    guest.image = image
    return guest


def _derivatives(builder, machine, guest_pair):
    port, volume = mock.MagicMock(), mock.MagicMock()
    volume.image = IMAGE
    with (
        mock.patch.object(
            builder, "_get_or_fetch_machine_ctx", return_value=(port, volume)
        ),
        mock.patch.object(pool_builder.ua_models, "UniversalAgent") as agent,
        mock.patch.object(pool_builder.models.Port, "from_boot_network"),
        mock.patch.object(
            pool_builder.pool_models.PoolMachine, "from_machine_and_port"
        ),
        mock.patch.object(pool_builder.pool_models, "GuestMachine"),
        mock.patch.object(builder, "_agent_by_pool", return_value=sys_uuid.uuid4()),
    ):
        agent.objects.get_one_or_none.return_value = mock.MagicMock()
        builder._actualize_machine_derivatives_on_create_update(
            machine, machine_guest_pair=guest_pair
        )
    return machine.boot


def test_an_update_keeps_a_machine_that_is_still_installing_on_the_network(builder):
    """Seed OS has not reported back yet: taking the machine off the network
    boot would rebuild it from a disk nothing has written."""
    machine = _machine()
    boot = _derivatives(
        builder, machine, guest_pair=(_guest(nc.BootAlternative.network), None)
    )
    assert boot == nc.BootAlternative.network.value


def test_an_update_leaves_an_installed_machine_on_its_disk(builder):
    machine = _machine()
    boot = _derivatives(
        builder,
        machine,
        guest_pair=(_guest(nc.BootAlternative.hd0), _guest(nc.BootAlternative.hd0)),
    )
    assert boot == nc.BootAlternative.hd0.value


def test_a_changed_image_sends_the_machine_back_to_the_network(builder):
    machine = _machine()
    boot = _derivatives(
        builder,
        machine,
        guest_pair=(
            _guest(nc.BootAlternative.hd0),
            _guest(nc.BootAlternative.hd0, image="http://repo/other.raw.zst"),
        ),
    )
    assert boot == nc.BootAlternative.network.value
