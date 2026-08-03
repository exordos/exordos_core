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

from unittest import mock
import uuid as sys_uuid
from xml.dom import minidom
from xml.etree import ElementTree as ET

import pytest

# The libvirt driver imports the `libvirt` python bindings at module level.
# They aren't always installed, so skip this module instead of failing
# collection when they're not available.
pytest.importorskip("libvirt")

from exordos_core.compute.dm import models  # noqa: E402
from exordos_core.compute.pool.drivers.libvirt import LibvirtPoolDriver  # noqa: E402
from exordos_core.compute.pool.drivers.libvirt import XMLLibvirtInstance  # noqa: E402
from exordos_core.compute.pool.drivers.libvirt import domain_template  # noqa: E402


def _local_driver() -> LibvirtPoolDriver:
    # libvirt's built-in "test" driver simulates a hypervisor in-memory -
    # no real virtualization or daemon needed, so real libvirt calls
    # (lookupByUUIDString, etc.) can be exercised end-to-end.
    spec = models.LibvirtPoolDriverSpec(connection_uri="test:///default")
    pool = models.MachinePool(uuid=sys_uuid.uuid4(), name="test-pool", driver_spec=spec)
    return LibvirtPoolDriver(pool)


def test_domain_console_logs_to_file():
    log_path = "/var/log/libvirt/qemu/test-vm.console.log"

    domain = XMLLibvirtInstance(domain_template)
    domain.set_console_log(log_path)

    console = ET.fromstring(domain.xml).find(".//devices/console")
    assert console is not None

    log = console.find("log")
    assert log is not None

    assert console.get("type") == "pty"
    assert log.get("file") == log_path
    assert log.get("append") == "on"


class TestRemoveDirectChildren:
    def test_removes_only_direct_children_leaving_nested_matches_alone(self):
        # getElementsByTagName searches the whole subtree recursively -
        # a naive removeChild(node) on a match found deeper in the tree
        # (not a direct child of root) raises NotFoundErr.
        doc = minidom.parseString("<root><a>direct</a><b><a>nested</a></b></root>")
        root = doc.firstChild

        XMLLibvirtInstance._remove_direct_children(root, "a")

        assert root.getElementsByTagName("a") == doc.getElementsByTagName("b")[
            0
        ].getElementsByTagName("a")
        assert len(doc.getElementsByTagName("a")) == 1
        assert doc.getElementsByTagName("a")[0].firstChild.data == "nested"

    def test_leaves_other_tag_names_alone(self):
        doc = minidom.parseString("<root><a>1</a><c>2</c></root>")
        root = doc.firstChild

        XMLLibvirtInstance._remove_direct_children(root, "a")

        assert len(doc.getElementsByTagName("a")) == 0
        assert len(doc.getElementsByTagName("c")) == 1

    def test_re_setting_a_tag_with_a_same_named_nested_element_does_not_crash(self):
        # Regression: domain_set_vcpu/domain_set_memory/etc. re-set their
        # tag on every call - this must not crash even if some unrelated
        # nested element happens to share the tag name.
        domain = XMLLibvirtInstance(domain_template)
        devices = ET.fromstring(domain.xml).find("devices")
        assert devices is not None  # sanity: domain_template has one

        domain.set_vcpu(2)
        domain.set_vcpu(4)
        domain.set_memory(1024)
        domain.set_memory(2048)

        element = ET.fromstring(domain.xml)
        assert element.find(".//vcpu").text == "4"
        assert element.find(".//currentMemory").text == "2048"


class TestDeleteMachine:
    def test_is_idempotent_when_the_domain_is_already_gone(self):
        driver = _local_driver()
        machine = models.Machine(
            uuid=sys_uuid.uuid4(),
            project_id=sys_uuid.uuid4(),
            name="never-existed",
            cores=1,
            ram=512,
        )

        # Must not raise, even though no such domain was ever defined.
        driver.delete_machine(machine, delete_volumes=False)

    def test_volume_cleanup_still_runs_when_the_domain_is_already_gone(self):
        driver = _local_driver()
        machine = models.Machine(
            uuid=sys_uuid.uuid4(),
            project_id=sys_uuid.uuid4(),
            name="never-existed",
            cores=1,
            ram=512,
        )

        # The missing-domain path must fall through to volume cleanup,
        # not skip it.
        with mock.patch.object(
            driver, "list_volumes", return_value=[]
        ) as mock_list_volumes:
            driver.delete_machine(machine, delete_volumes=True)

        mock_list_volumes.assert_called_once_with(machine)


def test_interface_xml_ovs_virtualport_carries_iface_id():
    xml = XMLLibvirtInstance.interface_xml(
        iface_type="bridge",
        source="br-int",
        mac="52:54:00:aa:bb:cc",
        interface_id="11111111-2222-3333-4444-555555555555",
    )
    iface = ET.fromstring(xml)
    assert iface.find("source").get("bridge") == "br-int"
    vport = iface.find("virtualport")
    assert vport is not None and vport.get("type") == "openvswitch"
    params = vport.find("parameters")
    assert params.get("interfaceid") == "11111111-2222-3333-4444-555555555555"


def test_interface_xml_ovs_port_carries_no_filterref():
    """libvirt refuses a domain with both a filterref and an openvswitch
    virtualport, so an overlay port must not carry one — it would never
    start."""
    xml = XMLLibvirtInstance.interface_xml(
        iface_type="bridge",
        source="br-int",
        mac="52:54:00:aa:bb:cc",
        interface_id="11111111-2222-3333-4444-555555555555",
        port_security=True,
        ipv4="10.100.0.10",
    )
    assert ET.fromstring(xml).find("filterref") is None


def test_interface_xml_bridge_port_keeps_its_filterref():
    xml = XMLLibvirtInstance.interface_xml(
        iface_type="bridge",
        source="br0",
        mac="52:54:00:aa:bb:cc",
        port_security=True,
        ipv4="10.100.0.10",
    )
    filterref = ET.fromstring(xml).find("filterref")
    assert filterref is not None and filterref.get("filter") == "clean-traffic"


def test_interface_xml_without_id_has_no_virtualport():
    xml = XMLLibvirtInstance.interface_xml(
        iface_type="bridge", source="br0", mac="52:54:00:aa:bb:cc"
    )
    assert ET.fromstring(xml).find("virtualport") is None


def _driver_with(spec_kwargs, existing_networks=(), overlay=None):
    from unittest import mock as _mock

    from exordos_core.compute.dm import models as dm
    from exordos_core.compute.pool.drivers import libvirt as lv

    drv = object.__new__(lv.LibvirtPoolDriver)
    drv._spec = dm.LibvirtPoolDriverSpec(
        network="exordos-core-net",
        storage_pool="default",
        connection_uri="qemu:///system",
        **spec_kwargs,
    )

    client = _mock.MagicMock()

    def lookup(name):
        if name in existing_networks:
            return _mock.MagicMock()
        raise lv.libvirt.libvirtError("no network")

    client.networkLookupByName.side_effect = lookup
    drv._client_instance = client
    client.isAlive.return_value = 1
    return drv


def _port(source, overlay=False):
    from exordos_core.compute.dm import models as dm

    port = dm.Port(
        project_id=sys_uuid.UUID("12345678-c625-4fee-81d5-f691897b8142"),
        source=source,
        mac=dm.Port.generate_mac(),
    )
    port.overlay = overlay
    return port


def test_iface_args_overlay_port_goes_to_br_int():
    """A port of an overlay network: bridge br-int + openvswitch
    virtualport (iface-id)."""

    drv = _driver_with({"ovs": True})
    port = _port("private", overlay=True)
    args = drv._iface_args(port)
    assert args["iface_type"] == "bridge"
    assert args["source"] == "br-int"
    assert args["interface_id"] == str(port.uuid)


def test_iface_args_boot_port_keeps_network_path():
    """A boot/flat port must NOT get a virtualport even on an ovs pool —
    it would break the netboot NIC."""

    drv = _driver_with({"ovs": True})
    port = _port("exordos-core-boot-net")
    args = drv._iface_args(port)
    assert args["iface_type"] == "network"
    assert args["source"] == "exordos-core-boot-net"
    assert args["interface_id"] is None


def test_iface_args_flat_subnet_without_libvirt_network_is_not_overlay():
    """The regression that replaced a guess with an answer.

    A subnet of a flat network that no libvirt network is named after used
    to be mistaken for an overlay port and plugged into br-int, where
    nothing is wired — silently, the guest simply unreachable. It has to
    take the classic path, so libvirt refuses the unknown network aloud.
    """

    drv = _driver_with({"ovs": True})
    port = _port("fip-pool")
    args = drv._iface_args(port)
    assert args["iface_type"] == "network"
    assert args["source"] == "fip-pool"
    assert args["interface_id"] is None


def test_iface_args_non_ovs_pool_unchanged():
    drv = _driver_with({})
    port = _port("private", overlay=True)
    args = drv._iface_args(port)
    assert args["iface_type"] == "network"
    assert args["interface_id"] is None


def test_is_overlay_port_reads_what_the_control_plane_sent():
    """The agent rebuilds a port from a few fields and a placeholder
    subnet, so the answer has to travel with it. Absent, the classic
    path is the safe reading."""
    drv = _driver_with({"ovs": True})

    assert drv._is_overlay_port(_port("private", overlay=True)) is True
    assert drv._is_overlay_port(_port("private", overlay=False)) is False

    from exordos_core.compute.dm import models as dm

    bare = dm.Port(
        project_id=sys_uuid.UUID("12345678-c625-4fee-81d5-f691897b8142"),
        source="private",
        mac=dm.Port.generate_mac(),
    )
    assert drv._is_overlay_port(bare) is False


def test_port_info_carries_the_overlay_answer():
    """The control plane decides and sends it.

    Nothing downstream can work it out: the agent rebuilds the port from
    this dict alone, with a placeholder subnet and no network to consult.
    """
    from exordos_core.compute.agents.universal.drivers import pool as agent_pool
    from exordos_core.compute.pool.dm import models as pool_models

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

    agent_machine = agent_pool.MetaMachine(
        uuid=sys_uuid.uuid4(),
        name="guest",
        project_id=project,
        cores=1,
        ram=512,
        port_info=resource.port_info,
    )
    assert agent_machine._port().overlay is True
