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

"""What the pool builder makes of a node's ports (compute/builders/pool)."""

from types import SimpleNamespace
from unittest import mock
import uuid

from gcl_sdk.agents.universal.dm import models as ua_models

from exordos_core.compute import constants as nc
from exordos_core.compute.builders import pool as pool_builder
from exordos_core.compute.dm import models


def test_an_overlay_guest_installs_on_its_own_network():
    """A guest of an overlay has no way to reach the installation's boot
    network, and putting a foot of every tenant's guest on that shared
    segment for the length of an install is not a thing to do quietly. Its
    own hypervisor answers its DHCP, hands it a boot script and proxies the
    boot API — so it netboots through the network it will live on."""
    overlay = mock.MagicMock()
    overlay.network.driver_spec = {"driver": nc.OVERLAY_DRIVER}
    port = mock.MagicMock()
    port.subnet = overlay
    assert models.Port.is_overlay(port) is True

    # A port on a plain network still goes to the boot network to install.
    flat = mock.MagicMock()
    flat.network.driver_spec = {"driver": "flat_bridge"}
    port.subnet = flat
    assert models.Port.is_overlay(port) is False

    # ... and so does one whose network cannot be read at all.
    port.subnet = None
    assert models.Port.is_overlay(port) is False


def test_the_machine_is_built_with_the_leg_at_index_zero():
    """Index 0 is the primary leg, so it is the one handed to the hypervisor,
    whatever the query's row order."""

    ports = [mock.MagicMock(index=index) for index in (2, 0, 1)]
    builder = pool_builder.PoolBuilderService.__new__(pool_builder.PoolBuilderService)
    machine = mock.MagicMock()

    with (
        mock.patch.object(
            models.Port, "objects", **{"get_all.return_value": list(ports)}
        ),
        mock.patch.object(
            models.MachineVolume, "objects", **{"get_all.return_value": []}
        ),
    ):
        fetched, _volumes = builder._fetch_machine_deps(machine)

    assert [p.index for p in fetched] == [0, 1, 2]


def test_reimage_without_a_port_does_not_switch_the_guest_to_netboot():
    builder = pool_builder.PoolBuilderService.__new__(pool_builder.PoolBuilderService)
    machine = SimpleNamespace(uuid=uuid.uuid4(), name="guest", boot="hd0", image="old")
    builder._get_or_fetch_machine_ctx = mock.Mock(
        return_value=(None, SimpleNamespace(image="new"))
    )
    builder._resolve_image = mock.Mock(return_value="new")
    builder._is_core_machine = mock.Mock(return_value=False)
    with mock.patch.object(
        ua_models.UniversalAgent,
        "objects",
        **{"get_one_or_none.return_value": SimpleNamespace(uuid=uuid.uuid4())},
    ):
        result = builder._actualize_machine_derivatives_on_create_update(
            machine,
            machine_guest_pair=(None, SimpleNamespace(image="old")),
        )
    assert result == ()
    assert machine.boot == "hd0"
    assert machine.image == "old"
