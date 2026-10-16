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

"""What the control plane has to put in `port_info`.

The agent rebuilds a machine's port from this dict alone, so anything it
cannot derive has to travel. The agent side is tested in gcl_sdk.
"""

import pathlib
from unittest import mock
import uuid as sys_uuid

from gcl_sdk.agents.universal.drivers import pool as ua_pool_driver

from exordos_core.compute.dm import models
from exordos_core.compute.pool.dm import models as pool_models


def test_port_info_carries_the_overlay_answer():
    """The control plane says whether the interface is on the overlay.

    Downstream cannot tell: a flat subnet with no libvirt network of its name
    looks like an overlay port, and a mis-plugged guest fails silently.
    """
    project = sys_uuid.UUID("12345678-c625-4fee-81d5-f691897b8142")
    machine = mock.MagicMock(
        uuid=sys_uuid.uuid4(),
        project_id=project,
        cores=1,
        ram=512,
        image=None,
        machine_type="VM",
        boot="network",
    )
    machine.name = "guest"
    machine.node.uuid = sys_uuid.uuid4()
    machine.pool.uuid = sys_uuid.uuid4()
    port = models.Port(
        project_id=project,
        source="realm-abc",
        mac=models.Port.generate_mac(),
    )
    with mock.patch.object(models.Port, "is_overlay", return_value=True):
        resource = pool_models.PoolMachine.from_machine_and_port(machine, port)

    assert resource.port_info["overlay"] is True
    # The port uuid rides along too: the agent stamps it as the OVS
    # iface-id, which is how the SDN agent resolves the guest.
    assert resource.port_info["uuid"] == str(port.uuid)


def test_the_sdn_migration_bumps_the_pools_it_reshaped():
    """The SDN migration bumps every pool so it is re-emitted.

    A target resource is rebuilt only once `updated_at` passes
    `ua_target_resources.tracked_at`; a code change moves neither.
    """
    migrations = pathlib.Path(__file__).parents[4] / "migrations"
    (sdn_migration,) = migrations.glob("*-network-sdn-*.py")

    assert "UPDATE machine_pools SET updated_at" in sdn_migration.read_text()


def test_a_flat_port_sends_only_what_any_agent_gives_back():
    """A flat port's `port_info` holds only what any agent gives back.

    Pre-overlay agents keep only ipv4, mask, mac and source; an extra key keeps
    target and actual hashes apart forever.
    """
    project = sys_uuid.UUID("12345678-c625-4fee-81d5-f691897b8142")
    machine = mock.MagicMock(
        uuid=sys_uuid.uuid4(),
        project_id=project,
        cores=1,
        ram=512,
        image=None,
        machine_type="VM",
        boot="network",
    )
    machine.name = "guest"
    machine.node.uuid = sys_uuid.uuid4()
    machine.pool.uuid = sys_uuid.uuid4()
    port = models.Port(
        project_id=project,
        source="flat-subnet",
        mac=models.Port.generate_mac(),
    )
    with mock.patch.object(models.Port, "is_overlay", return_value=False):
        resource = pool_models.PoolMachine.from_machine_and_port(machine, port)

    assert set(resource.port_info) == {"ipv4", "mask", "mac", "source"}

    actual = ua_pool_driver.MetaMachine(
        uuid=machine.uuid,
        project_id=project,
        name="guest",
        port_info=dict(resource.port_info),
    )
    dp_machine = mock.MagicMock(
        cores=1, ram=512, status="ACTIVE", boot="network", image=None
    )
    dp_port = mock.MagicMock(mac=port.mac, source=port.source)
    actual._from_dp_machine(dp_machine, [dp_port])

    assert actual.port_info == resource.port_info


def test_a_pool_that_asks_for_no_overlay_is_sent_as_before():
    """A pool without an overlay is sent without `ovs`.

    A pre-overlay agent treats an unknown key as a parse error and would never
    accept the pool.
    """
    spec = ua_pool_driver.LibvirtPoolDriverSpec(connection_uri="qemu:///system")
    pool = models.MachinePool(name="pool", driver_spec=spec)

    assert "ovs" not in pool.dump_to_simple_view()["driver_spec"]

    spec = ua_pool_driver.LibvirtPoolDriverSpec(
        connection_uri="qemu:///system", ovs=True
    )
    pool = models.MachinePool(name="pool", driver_spec=spec)

    assert pool.dump_to_simple_view()["driver_spec"]["ovs"] is True
