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

from exordos_core.compute import constants as nc  # noqa: E402
from exordos_core.compute.dm import models  # noqa: E402
from exordos_core.compute.pool.drivers.libvirt import LibvirtPoolDriver  # noqa: E402
from exordos_core.compute.pool.drivers.libvirt import XMLLibvirtInstance  # noqa: E402
from exordos_core.compute.pool.drivers.libvirt import domain_template  # noqa: E402


def _local_driver(network_type: str = "network") -> LibvirtPoolDriver:
    # libvirt's built-in "test" driver simulates a hypervisor in-memory -
    # no real virtualization or daemon needed, so real libvirt calls
    # (lookupByUUIDString, etc.) can be exercised end-to-end. It even ships
    # a default storage pool ("default-pool"), so create_machine() can be
    # exercised end-to-end too.
    spec = models.LibvirtPoolDriverSpec(
        connection_uri="test:///default",
        network_type=network_type,
        storage_pool="default-pool",
    )
    pool = models.MachinePool(
        uuid=sys_uuid.uuid4(), name="test-pool", driver_spec=spec
    )
    return LibvirtPoolDriver(pool)


def _machine() -> models.Machine:
    return models.Machine(
        uuid=sys_uuid.uuid4(),
        project_id=sys_uuid.uuid4(),
        name="test-vm",
        cores=1,
        ram=512,
    )


def _port(uuid: sys_uuid.UUID, source: str) -> models.Port:
    return models.Port(
        uuid=uuid,
        project_id=sys_uuid.uuid4(),
        mac=models.Port.generate_mac(),
        source=source,
        status=nc.PortStatus.ACTIVE.value,
    )


def _live_interface(driver: LibvirtPoolDriver, machine: models.Machine) -> ET.Element:
    domain = driver._client.lookupByUUIDString(str(machine.uuid))
    return ET.fromstring(domain.XMLDesc()).find(".//devices/interface")


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


class TestCreateMachine:
    def test_boot_port_always_uses_network_type_on_a_bridge_hypervisor(self):
        # Regression: a bridge-type hypervisor must not have the boot
        # network's (logical, potentially long) name treated as a literal
        # host bridge device name - libvirt rejects that outright as too
        # long for IFNAMSIZ.
        driver = _local_driver(network_type="bridge")
        machine = _machine()
        port = _port(nc.BOOT_NETWORK_PORT_UUID, source="exordos-core-boot-net")

        driver.create_machine(machine, volumes=[], ports=[port])

        interface = _live_interface(driver, machine)
        assert interface.get("type") == "network"
        assert interface.find("source").get("network") == "exordos-core-boot-net"

    def test_real_port_honors_the_hypervisors_network_type(self):
        # Regression: on a real bridge-type hypervisor, ports are raw
        # bridge-type interfaces (source=<bridge device>) - not a libvirt
        # network wrapping one. create_machine() is also used by
        # recreate_machine() to rebuild the domain with the real port(s)
        # post-flash, so it must not force type='network' on those.
        driver = _local_driver(network_type="bridge")
        machine = _machine()
        port = _port(sys_uuid.uuid4(), source="br0")

        driver.create_machine(machine, volumes=[], ports=[port])

        interface = _live_interface(driver, machine)
        assert interface.get("type") == "bridge"
        assert interface.find("source").get("bridge") == "br0"


class TestAttachPort:
    def test_honors_the_hypervisors_network_type_for_bridge_hypervisors(self):
        # Regression: attach_port() is only ever used for the real port,
        # which on a bridge-type hypervisor is a raw bridge-type interface
        # (source=<bridge device>) - not a libvirt network wrapping one.
        #
        # libvirt's test:// driver doesn't support attachDeviceFlags()'s
        # LIVE+CONFIG flag combination, so mock the domain lookup instead
        # of exercising a real domain end-to-end.
        driver = _local_driver(network_type="bridge")
        machine = _machine()
        port = _port(sys_uuid.uuid4(), source="br0")

        mock_domain = mock.MagicMock()
        with mock.patch.object(
            driver._client, "lookupByUUIDString", return_value=mock_domain
        ):
            driver.attach_port(machine, port)

        interface_xml, _flags = mock_domain.attachDeviceFlags.call_args[0]
        interface = ET.fromstring(interface_xml)
        assert interface.get("type") == "bridge"
        assert interface.find("source").get("bridge") == "br0"
