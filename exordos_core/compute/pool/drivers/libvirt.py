#    Copyright 2025 Genesis Corporation.
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

import enum
import functools
import logging
import operator
import time
import typing as tp
import uuid as sys_uuid
from xml.dom import minidom

# It's more efficient to use ElementTree than minidom
from xml.etree import ElementTree as ET

import libvirt

from exordos_core.common import constants as c
from exordos_core.compute import constants as nc
from exordos_core.compute.dm import models
from exordos_core.compute.pool.drivers import base
from exordos_core.compute.pool.drivers import exceptions as pool_exc

ImageFormatType = tp.Literal["raw", "qcow2"]
NetworkType = tp.Literal["bridge", "network"]

MAX_VOLUME_INDEX = 4096
CONSOLE_LOG_DIR = "/var/log/libvirt/qemu"


class StoragePoolType(enum.Enum):
    DIR = "dir"
    ZFS = "zfs"

    @property
    def oversubscription_ratio(self) -> float:
        # FIXME(akremenetsky): Actual these values have to be
        # configurable but it's fine to start with some specific
        # values.
        if self == StoragePoolType.ZFS:
            return 10.0
        elif self == StoragePoolType.DIR:
            return 4.0

        raise ValueError(f"Unknown storage pool type: {self}")

    def volume_name(self, name: str) -> str:
        ext = self.volume_extension()
        if name.endswith(ext):
            return name
        return name + ext

    def legacy_volume_name(self, name: str, machine_uuid: sys_uuid.UUID) -> str:
        """Use only for backward compatibility with old volume naming."""
        ext = self.volume_extension()
        return f"{name}_{machine_uuid}{ext}"

    def volume_extension(self) -> str:
        if self == StoragePoolType.ZFS:
            return ""
        elif self == StoragePoolType.DIR:
            return ".qcow2"

        raise ValueError(f"Unknown storage pool type: {self}")


META_TAG = "genesis:genesis"
META_CPU_TAG = "genesis:vcpu"
META_MEM_TAG = "genesis:mem"
META_IMG_TAG = "genesis:image"
GENESIS_NS = "https://github.com/infraguys"

LOG = logging.getLogger(__name__)


def dry_run_decorator(return_index: int | str = 0):
    """
    Decorator that checks self._dry_run and logs method calls when in dry run mode.

    Args:
        return_index: Index (int) or name (str) of argument to return in dry run mode.
                     Default is 0 (first positional argument).
    """

    def decorator(func: tp.Callable) -> tp.Callable:
        @functools.wraps(func)
        def wrapper(self, *args: tp.Any, **kwargs: tp.Any) -> tp.Any:
            if getattr(self, "_dry_run", False):
                # Log method name and all parameters
                arg_strs = [repr(arg) for arg in args]
                kwarg_strs = [f"{k}={repr(v)}" for k, v in kwargs.items()]
                all_params = ", ".join(arg_strs + kwarg_strs)
                LOG.info("DRY RUN: %s(%s)", func.__name__, all_params)

                # For methods that return non-None values, return the specified argument
                import inspect

                sig = inspect.signature(func)
                # Check if method has a meaningful return annotation (not empty, not None)
                if (
                    sig.return_annotation != inspect.Signature.empty
                    and sig.return_annotation is not None
                ):
                    # Use sig.bind to properly map args/kwargs to parameters
                    bound = sig.bind(self, *args, **kwargs)
                    bound.apply_defaults()

                    # Determine which argument to return
                    if isinstance(return_index, str):
                        # Return argument by name from bound arguments
                        return bound.arguments.get(return_index)
                    elif isinstance(return_index, int):
                        # Get parameter name by index and return from bound arguments
                        param_names = list(sig.parameters.keys())
                        # Skip 'self' parameter (index 0)
                        if 0 <= return_index < len(param_names) - 1:
                            param_name = param_names[return_index + 1]
                            return bound.arguments.get(param_name)
                return None
            else:
                return func(self, *args, **kwargs)

        return wrapper

    return decorator


volume_template = """
<volume>
  <name>{name}</name>
  <capacity>{size}</capacity>
  <allocation>0</allocation>
</volume>
"""


volume_template_with_format = """
<volume>
  <name>{name}</name>
  <capacity>{size}</capacity>
  <allocation>0</allocation>
  <target>
    <format type="{format}"/>
  </target>
</volume>
"""


domain_template = """
<domain type="kvm">
  <metadata>
    <genesis:genesis xmlns:genesis="https://github.com/infraguys">
    </genesis:genesis>
  </metadata>
  <os>
    <type arch="x86_64" machine="q35">hvm</type>
  </os>
  <features>
    <acpi/>
    <apic/>
    <vmport state="off"/>
  </features>
  <cpu mode="host-passthrough"/>
  <clock offset="utc">
    <timer name="rtc" tickpolicy="catchup"/>
    <timer name="pit" tickpolicy="delay"/>
    <timer name="hpet" present="no"/>
  </clock>
  <pm>
    <suspend-to-mem enabled="no"/>
    <suspend-to-disk enabled="no"/>
  </pm>
  <devices>
    <emulator>/usr/bin/qemu-system-x86_64</emulator>
    <controller type="usb" model="qemu-xhci" ports="5"/>
    <controller type="pci" model="pcie-root"/>
    <!-- For hotplug devices -->
    <controller type="pci" model="pcie-root-port"/>
    <controller type="pci" model="pcie-root-port"/>
    <controller type="pci" model="pcie-root-port"/>
    <controller type="pci" model="pcie-root-port"/>
    <controller type="pci" model="pcie-root-port"/>
    <controller type="pci" model="pcie-root-port"/>
    <controller type="pci" model="pcie-root-port"/>
    <controller type="pci" model="pcie-root-port"/>
    <console type="pty"/>
    <channel type="unix">
      <source mode="bind"/>
      <target type="virtio" name="org.qemu.guest_agent.0"/>
    </channel>
    <channel type="spicevmc">
      <target type="virtio" name="com.redhat.spice.0"/>
    </channel>
    <input type="tablet" bus="usb"/>
    <graphics type="spice" port="-1" tlsPort="-1" autoport="yes">
      <image compression="off"/>
    </graphics>
    <video>
      <model type="qxl"/>
    </video>
    <redirdev bus="usb" type="spicevmc"/>
    <memballoon model="virtio"/>
    <rng model="virtio">
      <backend model="random">/dev/urandom</backend>
    </rng>
  </devices>
</domain>
"""


class XMLLibvirtMixin:
    @classmethod
    def add_element(
        cls,
        document: minidom.Document,
        tag_name: str,
        parent: tp.Optional[minidom.Element] = None,
        text: tp.Optional[str] = None,
        **kwargs,
    ) -> None:
        root = parent or document.firstChild
        element = document.createElement(tag_name)

        for name, value in kwargs.items():
            element.setAttribute(name, value)

        if text is not None:
            text_node = document.createTextNode(text)
            element.appendChild(text_node)

        root.appendChild(element)

    @classmethod
    def document_set_tag(
        cls,
        docement: minidom.Document,
        tag_name: str,
        text: tp.Optional[str] = None,
        meta_tag: tp.Optional[str] = None,
        parent: tp.Optional[minidom.Element] = None,
        **kwargs,
    ) -> None:
        root = parent or docement.firstChild
        # Firstly we need to remove the old value
        for node in root.getElementsByTagName(tag_name):
            root.removeChild(node)

        # Also we need to remove the old value from the meta
        if meta_tag is not None:
            meta_node = docement.getElementsByTagName(META_TAG)[0]
            for node in docement.getElementsByTagName(meta_tag):
                meta_node.removeChild(node)

            # Add the new value
            cls.add_element(docement, meta_tag, parent=meta_node, text=text)

        # Set the new value
        cls.add_element(docement, tag_name, parent=root, text=text, **kwargs)

    @classmethod
    def document_meta_set_tag(
        cls,
        docement: minidom.Document,
        tag: str,
        text: tp.Optional[str] = None,
        **kwargs,
    ) -> None:
        # Remove the old value from the meta
        meta_node = docement.getElementsByTagName(META_TAG)[0]
        for node in docement.getElementsByTagName(tag):
            meta_node.removeChild(node)

        # Add the new value
        cls.add_element(docement, tag, parent=meta_node, text=text, **kwargs)


class XMLLibvirtVolume(XMLLibvirtMixin):
    def __init__(self, volume: libvirt.virStorageVol | str):
        if isinstance(volume, libvirt.virStorageVol):
            self._volume = minidom.parseString(volume.XMLDesc())
        else:
            self._volume = minidom.parseString(volume)

    @property
    def xml(self) -> str:
        return self._volume.toxml()

    @classmethod
    def xml_from_base_template(
        cls, pool: libvirt.virStoragePool, name: str, size: int
    ) -> str:
        pool_xml = ET.fromstring(pool.XMLDesc())
        pool_type = StoragePoolType(pool_xml.get("type"))
        if pool_type.value == "zfs":
            return volume_template.format(name=name, size=size)

        return volume_template_with_format.format(
            name=f"{name}.qcow2", size=size, format="qcow2"
        )


class XMLLibvirtInstance(XMLLibvirtMixin):
    def __init__(self, domain: libvirt.virDomain | str):
        if isinstance(domain, libvirt.virDomain):
            self._domain = minidom.parseString(domain.XMLDesc())
        else:
            self._domain = minidom.parseString(domain)

    @property
    def xml(self) -> str:
        return self._domain.toxml()

    @classmethod
    def domain_set_name(cls, domain: minidom.Document, name: str) -> None:
        cls.document_set_tag(domain, "name", text=name)

    @classmethod
    def domain_set_uuid(cls, domain: minidom.Document, uuid: sys_uuid.UUID) -> None:
        cls.document_set_tag(domain, "uuid", text=str(uuid))

    @classmethod
    def domain_set_vcpu(cls, domain: minidom.Document, cores: int) -> None:
        cls.document_set_tag(
            domain,
            "vcpu",
            text=str(cores),
            meta_tag=META_CPU_TAG,
            placement="static",
        )

    @classmethod
    def domain_set_memory(cls, domain: minidom.Document, memory: int) -> None:
        cls.document_set_tag(
            domain,
            "memory",
            text=str(memory),
            meta_tag=META_MEM_TAG,
            unit="MiB",
        )
        cls.document_set_tag(
            domain,
            "currentMemory",
            text=str(memory),
            unit="MiB",
        )

    @classmethod
    def domain_set_image(cls, domain: minidom.Document, image: str) -> None:
        cls.document_meta_set_tag(
            domain,
            META_IMG_TAG,
            uri=image,
        )

    @classmethod
    def domain_set_boot(cls, domain: minidom.Document, boot: nc.BootType) -> None:
        os_element = domain.getElementsByTagName("os")[0]
        cls.document_set_tag(domain, "boot", parent=os_element, dev=boot)

    @classmethod
    def domain_set_console_log(cls, domain: minidom.Document, path: str) -> None:
        console_element = domain.getElementsByTagName("console")[0]
        console_element.setAttribute("type", "pty")

        for node in console_element.getElementsByTagName("log"):
            console_element.removeChild(node)

        cls.add_element(domain, "log", parent=console_element, file=path, append="on")

    @classmethod
    def disk_device_xml(
        cls,
        image_path: str,
        device: str = "vda",
        bus: str = "virtio",
    ) -> str:
        if image_path.startswith("/dev/zvol/"):
            base_xml = "<disk type='block' device='disk'></disk>"
            image_format = "raw"
            document = minidom.parseString(base_xml)
            cls.document_set_tag(document, "source", dev=image_path)
        else:
            base_xml = "<disk type='file' device='disk'></disk>"
            image_format = "qcow2"
            document = minidom.parseString(base_xml)
            cls.document_set_tag(document, "source", file=image_path)

        cls.document_set_tag(document, "target", dev=device, bus=bus)
        cls.document_set_tag(
            document,
            "driver",
            name="qemu",
            type=image_format,
            discard="unmap",  # Support trimming of unused blocks
        )

        return document.firstChild.toxml()

    @classmethod
    def domain_add_disk(
        cls,
        domain: minidom.Document,
        image_path: str,
        device: str = "vda",
        bus: str = "virtio",
    ) -> None:
        device_element = domain.getElementsByTagName("devices")[0]
        device_element.appendChild(
            minidom.parseString(cls.disk_device_xml(image_path, device, bus)).firstChild
        )

    @classmethod
    def interface_xml(
        cls,
        iface_type: NetworkType = "network",
        source: tp.Optional[str] = None,
        model: str = "virtio",
        mtu: int = 1450,
        mac: tp.Optional[str] = None,
        rom: tp.Optional[str] = None,
    ) -> str:
        interface = ET.Element("interface", type=iface_type)

        ET.SubElement(interface, "model", type=model)
        ET.SubElement(interface, "mtu", size=str(mtu))

        if rom is not None:
            ET.SubElement(interface, "rom", bar="on", file=rom)

        mac_address = mac or models.Port.generate_mac()
        ET.SubElement(interface, "mac", address=mac_address)

        if iface_type == "bridge":
            if source is None:
                raise ValueError("Source is required for bridge interface")
            ET.SubElement(interface, "source", bridge=source)
        elif iface_type == "network":
            if source is None:
                raise ValueError("Source is required for network interface")
            ET.SubElement(interface, "source", network=source)
        else:
            raise ValueError(f"Unsupported interface type: {iface_type}")

        return ET.tostring(interface, encoding="unicode")

    @classmethod
    def domain_add_interface(
        cls,
        domain: minidom.Document,
        iface_type: NetworkType = "network",
        source: tp.Optional[str] = None,
        model: str = "virtio",
        mtu: int = 1450,
        mac: tp.Optional[str] = None,
        rom: tp.Optional[str] = None,
    ) -> None:
        interface_xml = cls.interface_xml(
            iface_type=iface_type,
            source=source,
            model=model,
            mtu=mtu,
            mac=mac,
            rom=rom,
        )
        device_element = domain.getElementsByTagName("devices")[0]
        device_element.appendChild(minidom.parseString(interface_xml).firstChild)

    def set_name(self, name: str) -> None:
        return self.domain_set_name(self._domain, name)

    def set_uuid(self, uuid: sys_uuid.UUID) -> None:
        return self.domain_set_uuid(self._domain, uuid)

    def set_vcpu(self, cores: int) -> None:
        return self.domain_set_vcpu(self._domain, cores)

    def set_memory(self, memory: int) -> None:
        return self.domain_set_memory(self._domain, memory)

    def set_image(self, image: tp.Optional[str]) -> None:
        if image is None:
            return
        return self.domain_set_image(self._domain, image)

    def set_boot(self, boot: nc.BootType) -> None:
        return self.domain_set_boot(self._domain, boot)

    def set_console_log(self, path: str) -> None:
        return self.domain_set_console_log(self._domain, path)

    def add_disk(
        self,
        image_path: str,
        device: str = "vda",
        bus: str = "virtio",
    ) -> None:
        return self.domain_add_disk(self._domain, image_path, device, bus)

    def add_interface(
        self,
        iface_type: NetworkType = "network",
        source: tp.Optional[str] = None,
        model: str = "virtio",
        mtu: int = 1450,
        mac: tp.Optional[str] = None,
        rom: tp.Optional[str] = None,
    ) -> None:
        return self.domain_add_interface(
            self._domain,
            iface_type,
            source,
            model=model,
            rom=rom,
            mtu=mtu,
            mac=mac,
        )


class LibvirtPoolDriver(base.AbstractPoolDriver):
    def __init__(self, pool: models.MachinePool, dry_run: bool = False):
        if pool.driver_spec is None or not isinstance(
            pool.driver_spec, models.LibvirtPoolDriverSpec
        ):
            raise ValueError(
                f"Unsupported driver spec kind: "
                f"{pool.driver_spec.KIND if pool.driver_spec else None!r}"
            )
        self._spec = pool.driver_spec
        self._pool = pool
        # Check if connection string is valid and we can connect
        _ = self._client
        super().__init__(dry_run=dry_run)

    @property
    def _client(self):
        instance = getattr(self, "_client_instance", None)
        # isAlive() doesn't actually ping host (so it's cheap),
        #  but it will return 0 if there were any errors before
        if not instance or not instance.isAlive():
            instance = libvirt.open(self._spec.connection_uri)
            if not instance:
                raise ConnectionError(
                    f"Failed to open libvirt connection: {self._spec.connection_uri}"
                )
            self._client_instance = instance
        return instance

    def _create_domain(self, domain_xml: str) -> libvirt.virDomain:
        virt_domain = self._client.defineXML(domain_xml)
        virt_domain.create()

        # Set the autostart flag to run the domain
        # after hypervisor restart
        virt_domain.setAutostart(True)

        return virt_domain

    def _domain2machine_name(self, name: str) -> str:
        machine_prefix_len = len(self._spec.machine_prefix or "")
        uuid_prefix_len = 9
        return name[machine_prefix_len + uuid_prefix_len :]

    def _machine2domain_name(self, machine: models.Machine) -> str:
        machine_prefix = self._spec.machine_prefix or ""
        uuid_prefix = str(machine.uuid)[:8] + "-"
        return f"{machine_prefix}{uuid_prefix}{str(machine.name)}"

    def _console_log_path(self, machine: models.Machine) -> str:
        return f"{CONSOLE_LOG_DIR}/{self._machine2domain_name(machine)}.console.log"

    def _domain2machine(
        self, domain: libvirt.virDomain, element: tp.Optional[ET.Element] = None
    ) -> tp.Tuple[models.Machine, tp.Tuple[models.Port, ...]]:
        element = element or ET.fromstring(domain.XMLDesc())

        cores_xml = element.find(f".//{{{GENESIS_NS}}}vcpu")
        cores = int(cores_xml.text)
        ram_xml = element.find(f".//{{{GENESIS_NS}}}mem")
        ram = int(ram_xml.text)
        image_el = element.find(f".//{{{GENESIS_NS}}}image")
        image = image_el.get("uri") if image_el is not None else None

        # Determine the boot device. For the backward compatibility
        # the disk boot device is considered if such option is present
        # in the XML and not matter which order it has.
        boot = nc.BootAlternative.network.value
        for boot_el in element.findall(".//os/boot"):
            boot = boot_el.get("dev")
            if nc.BootAlternative.hd0.value.startswith(boot):
                boot = nc.BootAlternative.hd0.value
                break

        machine_uuid = sys_uuid.UUID(domain.UUIDString())

        # Extract network interfaces as ports
        ports = []
        for iface in element.findall(".//devices/interface"):
            mac_el = iface.find("mac")
            source_el = iface.find("source")
            mac = mac_el.get("address")
            # The boot-network port is always type='network' regardless of
            # the hypervisor's own network_type (see create_machine) - read
            # the source attribute this specific interface actually has
            # ('network' or 'bridge'), not the hypervisor's configured type.
            source = source_el.get(iface.get("type"))

            if not mac or not source:
                raise ValueError(f"Interface {iface} has no mac or source")

            ports.append(
                models.Port(
                    uuid=sys_uuid.UUID("00000000-0000-0000-0000-000000000000"),
                    machine=machine_uuid,
                    mac=mac,
                    project_id=c.SERVICE_PROJECT_ID,
                    source=source,
                    status=nc.PortStatus.ACTIVE.value,
                )
            )

        machine = models.Machine(
            uuid=machine_uuid,
            name=self._domain2machine_name(domain.name()),
            machine_type=nc.NodeType.VM.value,
            cores=cores,
            ram=ram,
            image=image,
            # TODO(akremenetsky): Form status from domain state
            status=nc.MachineStatus.ACTIVE.value,
            # These fields don't make sense for data plane entities
            pool=self._pool.uuid,
            project_id=c.SERVICE_PROJECT_ID,
            boot=boot,
        )

        return machine, tuple(ports)

    def _vir_volume_name(
        self,
        storage_pool: libvirt.virStoragePool,
        volume: models.MachineVolume,
    ) -> str:
        pool_xml = ET.fromstring(storage_pool.XMLDesc())
        pool_type = StoragePoolType(pool_xml.get("type"))

        return pool_type.volume_name(volume.name)

    def _volume_name(self, volume: libvirt.virStorageVol) -> str:
        return volume.name()[:36]

    def _vir_volume2machine_volume(
        self,
        volume: libvirt.virStorageVol,
        machine_uuid: tp.Optional[sys_uuid.UUID] = None,
        index: tp.Optional[int] = None,
    ) -> models.MachineVolume:
        index = index if index is not None else MAX_VOLUME_INDEX

        volume_name = self._volume_name(volume)
        volume_uuid = sys_uuid.UUID(volume_name)
        info = volume.info()

        return models.MachineVolume(
            uuid=volume_uuid,
            machine=machine_uuid,
            name=volume_name,
            project_id=c.SERVICE_PROJECT_ID,
            size=info[1] >> 30,  # in GB
            index=index,
            status=nc.VolumeStatus.ACTIVE.value,
        )

    def _list_interfaces(self, machine: models.Machine) -> tp.List[models.Port]:
        """List all interfaces of the machine."""
        domain = self._client.lookupByUUIDString(str(machine.uuid))
        element = ET.fromstring(domain.XMLDesc())

        # Extract network interfaces as ports
        ports = []
        for iface in element.findall(".//devices/interface"):
            mac_el = iface.find("mac")
            source_el = iface.find("source")
            mac = mac_el.get("address")
            # See _domain2machine: read the source attribute this specific
            # interface actually has, not the hypervisor's network_type.
            source = source_el.get(iface.get("type"))

            if not mac or not source:
                raise ValueError(f"Interface {iface} has no mac or source")

            ports.append(
                models.Port(
                    uuid=sys_uuid.UUID("00000000-0000-0000-0000-000000000000"),
                    machine=machine.uuid,
                    mac=mac,
                    project_id=c.SERVICE_PROJECT_ID,
                    source=source,
                    status=nc.PortStatus.ACTIVE.value,
                )
            )

        return ports

    def _volume_attachments(
        self,
        domains: tp.Collection[tp.Tuple[libvirt.virDomain, ET.Element]],
        volumes: tp.Collection[libvirt.virStorageVol],
    ) -> tp.Dict[libvirt.virStorageVol, tp.Optional[tp.Tuple[libvirt.virDomain, int]]]:
        result = {v: None for v in volumes}
        path_map = {v.path(): v for v in volumes}

        for domain, root in domains:
            idx = 0
            for disk in root.findall(".//devices/disk"):
                if disk.get("device") != "disk":
                    continue

                source = disk.find("source")
                if source is None:
                    LOG.warning("Unable to detect source for %s", ET.tostring(disk))
                    continue

                path = source.get("file") or source.get("dev")
                if path is None:
                    LOG.warning("Unable to detect path for %s", ET.tostring(disk))
                    continue

                volume = path_map.get(path)
                if volume is None:
                    LOG.warning("Unable to detect volume for path: %s", path)
                    continue

                result[volume] = (domain, idx)
                idx += 1

        return result

    def _list_volumes(
        self,
        domains: tp.Collection[tp.Tuple[libvirt.virDomain, ET.Element]],
        volumes: tp.Collection[libvirt.virStorageVol],
    ) -> tp.List[models.MachineVolume]:
        attachments = self._volume_attachments(domains, volumes)
        result = []

        for volume, attach_info in attachments.items():
            domain, idx = attach_info if attach_info else (None, None)
            machine_uuid = (
                None if domain is None else sys_uuid.UUID(domain.UUIDString())
            )
            try:
                result.append(
                    self._vir_volume2machine_volume(volume, machine_uuid, index=idx)
                )
            except Exception:
                LOG.debug("Failed to parse volume %s", volume.name())

        return result

    def _list_machines(
        self,
        domains: tp.Collection[tp.Tuple[libvirt.virDomain, tp.Optional[ET.Element]]],
    ) -> tp.List[tp.Tuple[models.Machine, tp.Tuple[models.Port, ...]]]:
        """Return machine list from data plane."""
        # If the filter prefix is not set, return all domains
        if not self._spec.machine_prefix:
            machines = [self._domain2machine(d, e) for d, e in domains]
            LOG.debug("Machines: %s", machines)
            return machines

        # Otherwise, filter domains by the prefix
        machines = []
        for d, e in domains:
            if d.name().startswith(self._spec.machine_prefix):
                machines.append(self._domain2machine(d, e))

        LOG.debug("Machines: %s", machines)
        return machines

    def _fill_thin_storage_pool(
        self,
        empty_pool: models.ThinStoragePool,
        volumes: tp.Collection[models.MachineVolume],
    ) -> models.ThinStoragePool:
        """List storage pools."""
        for volume in volumes:
            empty_pool.allocate_capacity(volume.size)

        empty_pool.oversubscription_ratio = StoragePoolType(
            empty_pool.pool_type
        ).oversubscription_ratio

        return empty_pool

    def _find_attached_volume_element(
        self, domain: ET.Element, volume: models.MachineVolume
    ) -> tp.Optional[ET.Element]:
        # Check the volume is attached to the domain
        for disk in domain.find("devices").findall("disk"):
            # Check source and path
            source_node = disk.find("source")
            if source_node is None:
                LOG.debug("No source for disk %s", disk)
                continue

            path = source_node.get("file") or source_node.get("dev")
            if not path:
                LOG.debug("No path for disk %s", disk)
                continue

            # FIXME(akremenetsky): Check volume name in path. We can check by
            # name as the name is actual UUID of the volume.
            if volume.name in path:
                return disk

        return None

    def _is_legacy_domain(self, domain: ET.Element) -> bool:
        """Determine if a domain uses legacy disk configuration."""

        for disk in domain.findall(".//devices/disk"):
            if disk.get("device") != "disk":
                continue

            source = disk.find("source")
            if source is None:
                LOG.warning("Unable to detect source for %s", ET.tostring(disk))
                continue

            path = source.get("file") or source.get("dev")
            if path is None:
                LOG.warning("Unable to detect path for %s", ET.tostring(disk))
                continue

            # Legacy volume name format is:
            # <volume_uuid>_<machine_uuid>[suffix]?
            name = path.split("/")[-1]
            try:
                vol, machine = name.split("_")
                sys_uuid.UUID(vol)
                sys_uuid.UUID(machine[:36])
                return True
            except Exception:
                return False

        return False

    def get_pool_info(self) -> models.MachinePool:
        """Get pool info."""
        info = self._client.getInfo()
        return models.MachinePool(
            driver_spec=self._spec,
            all_cores=info[2],
            all_ram=info[1],
        )

    def list_pool_resources(
        self,
    ) -> tp.Tuple[
        models.MachinePool,
        tp.Collection[tp.Tuple[models.Machine, tp.Tuple[models.Port, ...]]],
        tp.Collection[models.MachineVolume],
    ]:
        pool = self.get_pool_info()
        vir_storage_pool = self._client.storagePoolLookupByName(self._spec.storage_pool)
        volumes = vir_storage_pool.listAllVolumes()
        domains = tuple(
            (d, ET.fromstring(d.XMLDesc())) for d in self._client.listAllDomains()
        )

        volumes = self._list_volumes(domains, volumes)
        machines = self._list_machines(domains)

        storage_pool_element = ET.fromstring(vir_storage_pool.XMLDesc())
        pool_type = storage_pool_element.get("type")

        storage_pool = models.ThinStoragePool(
            uuid=sys_uuid.UUID(vir_storage_pool.UUIDString()),
            name=vir_storage_pool.name(),
            capacity_usable=vir_storage_pool.info()[1] >> 30,  # GB
            available_actual=vir_storage_pool.info()[3] >> 30,  # GB
            pool_type=pool_type,
        )

        self._fill_thin_storage_pool(storage_pool, volumes)

        return pool, (storage_pool,), machines, volumes

    def list_volumes(
        self, machine: tp.Optional[models.Machine] = None
    ) -> tp.Iterable[models.MachineVolume]:
        storage_pool = self._client.storagePoolLookupByName(self._spec.storage_pool)
        volumes = storage_pool.listAllVolumes()
        domains = tuple(
            (d, ET.fromstring(d.XMLDesc())) for d in self._client.listAllDomains()
        )

        volumes = self._list_volumes(domains, volumes)

        if machine is None:
            return volumes

        # Return volumes sorted by index for specific machine.
        # Otherwise volumes can be shuffled during machine recreation.
        machine_volumes = [v for v in volumes if v.machine == machine.uuid]
        machine_volumes.sort(key=operator.attrgetter("index"))
        return machine_volumes

    def get_volume(self, volume: sys_uuid.UUID) -> models.MachineVolume:
        storage_pool = self._client.storagePoolLookupByName(self._spec.storage_pool)
        pool_xml = ET.fromstring(storage_pool.XMLDesc())
        pool_type = StoragePoolType(pool_xml.get("type"))
        name = pool_type.volume_name(str(volume))

        # Firstly try the name directly
        try:
            vir_volume = storage_pool.storageVolLookupByName(name)
            # Explicitly pass None as the machine UUID
            return self._vir_volume2machine_volume(vir_volume, None)
        except libvirt.libvirtError as e:
            if e.get_error_code() != libvirt.VIR_ERR_NO_STORAGE_VOL:
                raise

        # If the volume is not found, perhaps it has a legacy name format
        volumes = storage_pool.listAllVolumes()
        for v in volumes:
            if v.name().startswith(str(volume)):
                return self._vir_volume2machine_volume(v, None)

        raise pool_exc.VolumeNotFoundError(volume=volume)

    @dry_run_decorator()
    def create_volume(self, volume: models.MachineVolume) -> models.MachineVolume:
        storage_pool = self._client.storagePoolLookupByName(self._spec.storage_pool)

        # TODO(akremenetsky): Rework `xml_from_base_template` to use
        # the correct name format
        volume_xml = XMLLibvirtVolume.xml_from_base_template(
            storage_pool, volume.name, volume.size << 30
        )

        try:
            storage_pool.createXML(volume_xml)
        except libvirt.libvirtError as e:
            if e.get_error_code() == libvirt.VIR_ERR_STORAGE_VOL_EXIST:
                raise pool_exc.VolumeAlreadyExistsError(volume=volume.uuid)
            raise

        # If no error the volume is ready
        volume.status = nc.VolumeStatus.ACTIVE.value

        # Workaround until https://gitlab.com/libvirt/libvirt/-/commit/29f3c67837cc10dca3023f0bfd50414244c1bbc3
        pool_xml = ET.fromstring(storage_pool.XMLDesc())
        pool_type = StoragePoolType(pool_xml.get("type"))
        if pool_type.value == "zfs":
            if storage_pool.isActive():
                storage_pool.refresh()
                LOG.warning(
                    "Due to libvirt<12.4 bug, ZFS storage pool was explicitly refreshed."
                )

        LOG.debug("The volume %s has been created", volume.uuid)
        return volume

    @dry_run_decorator()
    def delete_volume(self, volume: models.MachineVolume) -> None:
        storage_pool = self._client.storagePoolLookupByName(self._spec.storage_pool)
        name = self._vir_volume_name(storage_pool, volume)

        try:
            v = storage_pool.storageVolLookupByName(name)
        except libvirt.libvirtError:
            LOG.exception("The volume %s has not been found:", volume.uuid)
            return

        try:
            v.wipe()
        except libvirt.libvirtError as e:
            # Some backends don't need wiping, for ex. ZFS
            if e.get_error_code() != 3:  # VIR_ERR_NO_SUPPORT
                raise
        max_iters = 20
        for i in range(max_iters + 1):
            try:
                v.delete()
            except libvirt.libvirtError:
                if i == max_iters:
                    raise
                # Volume may be busy, just wait a little bit
                time.sleep(0.05)
            else:
                break
        LOG.debug("The volume %s has been deleted", v.name())
        return

    @dry_run_decorator()
    def attach_volume(self, volume: models.MachineVolume) -> None:
        """Attach the volume."""
        if volume.machine is None:
            raise ValueError("Cannot attach volume without machine")

        # Lookup domain by machine UUID
        try:
            domain = self._client.lookupByUUIDString(str(volume.machine))
        except libvirt.libvirtError as e:
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                raise pool_exc.MachineNotFoundError(machine=volume.machine)
            raise

        domain_xml = domain.XMLDesc()
        domain_element = ET.fromstring(domain_xml)

        # Check the volume isn't attached to the machine
        # FIXME(akremenetsky): Use the simplest check by UUID
        # FIXME(akremenetsky): Check volume name in path. We can check by
        # name as the name is actual UUID of the volume.
        disks = domain_element.find("devices").findall("disk")
        for disk in disks:
            if bytes(volume.name, "utf-8") in ET.tostring(disk):
                raise pool_exc.VolumeAlreadyAttachedError(
                    volume=volume.uuid, machine=volume.machine
                )

        # Lookup storage pool and volume
        storage_pool = self._client.storagePoolLookupByName(self._spec.storage_pool)
        volume_name = self._vir_volume_name(storage_pool, volume)

        try:
            vir_volume = storage_pool.storageVolLookupByName(volume_name)
        except libvirt.libvirtError as e:
            if e.get_error_code() == libvirt.VIR_ERR_NO_STORAGE_VOL:
                raise pool_exc.VolumeNotFoundError(volume=volume.uuid)
            raise

        # Detect the image path
        image_path = vir_volume.path()

        # Detect next device name from domain XML
        devices = len(domain_element.find("devices").findall("disk"))
        device_name = "vd" + chr(ord("a") + devices)

        disk_xml = XMLLibvirtInstance.disk_device_xml(image_path, device_name)

        # Attach the device both to live domain and persistent config
        flags = libvirt.VIR_DOMAIN_AFFECT_LIVE | libvirt.VIR_DOMAIN_AFFECT_CONFIG
        try:
            domain.attachDeviceFlags(disk_xml, flags)
        except libvirt.libvirtError as e:
            # If the disk is already attached or the operation is invalid
            # for the current domain state, treat it as "already attached".
            if e.get_error_code() == libvirt.VIR_ERR_OPERATION_INVALID:
                raise pool_exc.VolumeAlreadyAttachedError(
                    volume=volume.uuid,
                    machine=volume.machine,
                )
            raise

    @dry_run_decorator()
    def detach_volume(self, volume: models.MachineVolume) -> None:
        """Detach the volume."""
        if volume.machine is None:
            raise ValueError("Cannot detach volume without machine")

        # Lookup domain by machine UUID
        try:
            domain = self._client.lookupByUUIDString(str(volume.machine))
        except libvirt.libvirtError as e:
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                raise pool_exc.MachineNotFoundError(machine=volume.machine)
            raise

        domain_xml = domain.XMLDesc()
        domain_element = ET.fromstring(domain_xml)

        # Check the volume is attached to the domain
        disk = self._find_attached_volume_element(domain_element, volume)
        if disk is None:
            raise pool_exc.VolumeNotAttachedError(
                volume=volume.uuid, machine=volume.machine
            )

        # Detach the device both from live domain and persistent config
        flags = libvirt.VIR_DOMAIN_AFFECT_LIVE | libvirt.VIR_DOMAIN_AFFECT_CONFIG

        try:
            domain.detachDeviceFlags(ET.tostring(disk, "unicode"), flags)
        except libvirt.libvirtError as e:
            # If the disk is already detached or the operation is invalid
            # for the current domain state, treat it as "not attached".
            if e.get_error_code() == libvirt.VIR_ERR_OPERATION_INVALID:
                raise pool_exc.VolumeNotAttachedError(
                    volume=volume.uuid,
                    machine=volume.machine,
                )
            raise

    @dry_run_decorator()
    def resize_volume(self, volume: models.MachineVolume) -> None:
        """Resize the volume."""
        storage_pool = self._client.storagePoolLookupByName(self._spec.storage_pool)
        volume_name = self._vir_volume_name(storage_pool, volume)

        try:
            vir_volume = storage_pool.storageVolLookupByName(volume_name)
        except libvirt.libvirtError as e:
            if e.get_error_code() == libvirt.VIR_ERR_NO_STORAGE_VOL:
                raise pool_exc.VolumeNotFoundError(volume=volume.uuid)
            raise

        # libvirt expects size in bytes, our size is in GiB
        new_size_bytes = volume.size << 30

        # For safety, do not allow shrinking volumes for now
        info = vir_volume.info()
        current_size_bytes = info[1]
        if new_size_bytes < current_size_bytes:
            raise ValueError("Shrinking volumes is not supported")

        # If the volume is attached and a domain is active use
        # `blockResize`
        while volume.machine is not None:
            domain = self._client.lookupByUUIDString(str(volume.machine))

            # Not active, break
            if domain.isActive() == 0:
                break

            domain_xml = domain.XMLDesc()
            domain_element = ET.fromstring(domain_xml)
            disk = self._find_attached_volume_element(domain_element, volume)
            if disk is None:
                raise pool_exc.VolumeNotAttachedError(
                    volume=volume.uuid, machine=volume.machine
                )
            dev = disk.find("target").get("dev")

            pool_xml = ET.fromstring(storage_pool.XMLDesc())
            pool_type = StoragePoolType(pool_xml.get("type"))

            # ZFS zvols are block devices; QEMU cannot grow them directly.
            # Resize the zvol first, then notify the running guest.
            if pool_type == StoragePoolType.ZFS:
                vir_volume.resize(new_size_bytes)

            domain.blockResize(
                dev, new_size_bytes, libvirt.VIR_DOMAIN_BLOCK_RESIZE_BYTES
            )
            return

        # Ordinary resize via qemu-img
        vir_volume.resize(new_size_bytes)

    @dry_run_decorator()
    def attach_port(self, machine: models.Machine, port: models.Port) -> None:
        """Attach the port to the machine."""
        # Lookup domain by machine UUID
        try:
            domain = self._client.lookupByUUIDString(str(machine.uuid))
        except libvirt.libvirtError as e:
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                raise pool_exc.MachineNotFoundError(machine=machine.uuid)
            raise

        # Build interface XML
        interface_xml = XMLLibvirtInstance.interface_xml(
            iface_type=self._spec.network_type,
            mac=port.mac,
            rom=self._spec.iface_rom_file,
            mtu=self._spec.iface_mtu,
            source=port.source or self._spec.iface_source,
        )

        # Attach the interface both to live domain and persistent config
        flags = libvirt.VIR_DOMAIN_AFFECT_LIVE | libvirt.VIR_DOMAIN_AFFECT_CONFIG
        try:
            domain.attachDeviceFlags(interface_xml, flags)
        except libvirt.libvirtError as e:
            if e.get_error_code() == libvirt.VIR_ERR_OPERATION_INVALID:
                raise pool_exc.PortAlreadyAttachedError(
                    port=port.uuid,
                    machine=machine.uuid,
                )
            raise

    @dry_run_decorator()
    def detach_port(self, machine: models.Machine, port: models.Port) -> None:
        """Detach the port from the machine."""
        # Lookup domain by machine UUID
        try:
            domain = self._client.lookupByUUIDString(str(machine.uuid))
        except libvirt.libvirtError as e:
            if e.get_error_code() == libvirt.VIR_ERR_NO_DOMAIN:
                raise pool_exc.MachineNotFoundError(machine=machine.uuid)
            raise

        domain_xml = domain.XMLDesc()
        domain_element = ET.fromstring(domain_xml)

        # Find the interface with matching MAC address
        devices = domain_element.find("devices")
        interfaces = devices.findall("interface")

        target_interface = None
        for interface in interfaces:
            mac_element = interface.find("mac")
            if mac_element is not None and mac_element.get("address") == port.mac:
                target_interface = interface
                break

        if target_interface is None:
            raise pool_exc.PortNotAttachedError(
                port=port.uuid,
                machine=machine.uuid,
            )

        # Detach the interface both from live domain and persistent config
        flags = libvirt.VIR_DOMAIN_AFFECT_LIVE | libvirt.VIR_DOMAIN_AFFECT_CONFIG

        try:
            domain.detachDeviceFlags(ET.tostring(target_interface, "unicode"), flags)
        except libvirt.libvirtError as e:
            if e.get_error_code() == libvirt.VIR_ERR_OPERATION_INVALID:
                raise pool_exc.PortNotAttachedError(
                    port=port.uuid,
                    machine=machine.uuid,
                )
            raise

    def list_machines(
        self,
    ) -> tp.List[tp.Tuple[models.Machine, tp.Tuple[models.Port, ...]]]:
        """Return machine list from data plane."""
        domains = self._client.listAllDomains()
        return self._list_machines(tuple((d, None) for d in domains))

    def create_machine(
        self,
        machine: models.Machine,
        volumes: tp.Iterable[models.MachineVolume],
        ports: tp.Iterable[models.Port],
        legacy_machine: bool = False,
    ) -> tp.Tuple[models.Machine, tp.Tuple[models.Port, ...]]:
        """Create a new LibVirt domain."""
        # NOTE(akremenetsky): Unable to apply the dry_run decorator because of
        # the complex return type
        if self._dry_run:
            return machine, tuple(ports)

        domain = XMLLibvirtInstance(domain_template)

        # Set base characteristics for the domain
        domain.set_uuid(machine.uuid)
        domain.set_name(self._machine2domain_name(machine))
        domain.set_vcpu(machine.cores)
        domain.set_memory(machine.ram)
        domain.set_image(machine.image)
        domain.set_boot(nc.BootAlternative[machine.boot].boot_type)
        domain.set_console_log(self._console_log_path(machine))

        for port in ports:
            domain.add_interface(
                mac=port.mac,
                rom=self._spec.iface_rom_file,
                mtu=self._spec.iface_mtu,
                # The port a machine is created with is always the transient
                # boot network (see pool.py's MetaMachine.dump_to_dp), which
                # is always a plain libvirt virtual network - never the
                # hypervisor's own (possibly bridge-type) main network. The
                # real port replaces it later via attach_port(), which does
                # use self._spec.network_type.
                iface_type="network",
                source=port.source,
            )

        # Prepare volume paths
        storage_pool = self._client.storagePoolLookupByName(self._spec.storage_pool)

        storage_pool_xml = ET.fromstring(storage_pool.XMLDesc())
        pool_type = StoragePoolType(storage_pool_xml.get("type"))
        pool_path = storage_pool_xml.find("target").find("path").text

        # Add the volumes to the domain
        for i, volume in enumerate(volumes):
            if not legacy_machine:
                domain.add_disk(
                    image_path=f"{pool_path}/{pool_type.volume_name(volume.name)}",
                    device="vd" + chr(ord("a") + i),
                    bus="virtio",
                )
            else:
                # TODO(akremenetsky): Remove this snippet one day
                legacy_volume_name = pool_type.legacy_volume_name(
                    volume.name, machine.uuid
                )
                domain.add_disk(
                    image_path=f"{pool_path}/{legacy_volume_name}",
                    device="vd" + chr(ord("a") + i),
                    bus="virtio",
                )

        # Create the domain from the XML specification
        domain_spec = domain.xml
        self._create_domain(domain_spec)

        LOG.debug(
            "The domain %s has been created\nDomain spec:\n%s",
            machine.uuid,
            domain_spec,
        )
        return machine, tuple(ports)

    @dry_run_decorator()
    def delete_machine(
        self, machine: models.Machine, delete_volumes: bool = True
    ) -> None:
        """
        Delete a machine from the data plane.

        :param machine: The machine to delete
        """
        domain = self._client.lookupByUUIDString(str(machine.uuid))

        # Remove the libvirt domain
        try:
            domain.destroy()
        except libvirt.libvirtError:
            LOG.debug("The domain is not in the running state")
        # FIXME(akremenetsky): Actully we should undefine the
        # domain before volume deletion
        domain.undefine()

        if delete_volumes:
            for volume in self.list_volumes(machine):
                self.delete_volume(volume)

        LOG.debug("The domain %s has been destroyed", machine.uuid)

    def get_machine(
        self, machine: sys_uuid.UUID
    ) -> tp.Tuple[models.Machine, tp.Tuple[models.Port, ...]]:
        """Get machine from data plane."""
        domain = self._client.lookupByUUIDString(str(machine))
        return self._domain2machine(domain)

    @dry_run_decorator()
    def set_machine_cores(self, machine: models.Machine, cores: int) -> None:
        """Set machine cores."""
        ports = self._list_interfaces(machine)
        volumes = self.list_volumes(machine)

        # TODO(akremenetsky): It's only for backward compatibility
        # This part has to be removed once all machines are migrated.
        # Or this part can be reworked to convert legacy to new format.
        domain = self._client.lookupByUUIDString(str(machine.uuid))
        legacy_domain = self._is_legacy_domain(ET.fromstring(domain.XMLDesc()))

        self.delete_machine(machine, delete_volumes=False)

        machine.cores = cores
        self.create_machine(
            machine,
            volumes=volumes,
            ports=ports,
            legacy_machine=legacy_domain,
        )
        LOG.debug("The domain %s was updated with cores %s", machine.uuid, cores)

    @dry_run_decorator()
    def set_machine_ram(self, machine: models.Machine, ram: int) -> None:
        """Set machine ram."""
        ports = self._list_interfaces(machine)
        volumes = self.list_volumes(machine)

        # TODO(akremenetsky): It's only for backward compatibility
        # This part has to be removed once all machines are migrated.
        # Or this part can be reworked to convert legacy to new format.
        domain = self._client.lookupByUUIDString(str(machine.uuid))
        legacy_domain = self._is_legacy_domain(ET.fromstring(domain.XMLDesc()))

        self.delete_machine(machine, delete_volumes=False)

        machine.ram = ram
        self.create_machine(
            machine,
            volumes=volumes,
            ports=ports,
            legacy_machine=legacy_domain,
        )
        LOG.debug("The domain %s was updated with ram %s", machine.uuid, ram)

    @dry_run_decorator()
    def reset_machine(self, machine: models.Machine) -> None:
        """Reset the machine."""
        domain = self._client.lookupByUUIDString(str(machine.uuid))

        try:
            domain.destroy()
        except libvirt.libvirtError:
            LOG.debug("The domain is not in the running state")

        domain.create()
        LOG.debug("The domain %s was reset", str(machine.uuid))

    @dry_run_decorator()
    def shutdown_machine(self, machine: models.Machine, force: bool = False) -> None:
        """Shutdown the machine."""
        domain = self._client.lookupByUUIDString(str(machine.uuid))
        if force:
            domain.destroy()
        else:
            domain.shutdown()
        LOG.debug("The domain %s was shutdown(forced=%s)", machine.uuid, force)

    @dry_run_decorator()
    def start_machine(self, machine: models.Machine) -> None:
        """Start the machine."""
        domain = self._client.lookupByUUIDString(str(machine.uuid))
        domain.create()
        LOG.debug("The domain %s was started", machine.uuid)

    @dry_run_decorator()
    def rename_machine(self, machine: models.Machine, name: str) -> None:
        """Rename the machine."""
        origin_name = machine.name
        try:
            ports = self._list_interfaces(machine)
            volumes = self.list_volumes(machine)

            # TODO(akremenetsky): It's only for backward compatibility
            # This part has to be removed once all machines are migrated.
            # Or this part can be reworked to convert legacy to new format.
            domain = self._client.lookupByUUIDString(str(machine.uuid))
            legacy_domain = self._is_legacy_domain(ET.fromstring(domain.XMLDesc()))

            self.delete_machine(machine, delete_volumes=False)
            machine.name = name
            self.create_machine(
                machine,
                volumes=volumes,
                ports=ports,
                legacy_machine=legacy_domain,
            )
        except:
            machine.name = origin_name
            raise
        LOG.debug("The domain %s was renamed to %s", origin_name, name)

    @dry_run_decorator()
    def recreate_machine(
        self,
        machine: models.Machine,
        ports: tp.Optional[tp.Collection[models.Port]] = None,
    ) -> None:
        """Recreate the machine."""
        if ports is None:
            ports = self._list_interfaces(machine)

        volumes = self.list_volumes(machine)

        # TODO(akremenetsky): It's only for backward compatibility
        # This part has to be removed once all machines are migrated.
        # Or this part can be reworked to convert legacy to new format.
        domain = self._client.lookupByUUIDString(str(machine.uuid))
        legacy_domain = self._is_legacy_domain(ET.fromstring(domain.XMLDesc()))

        self.delete_machine(machine, delete_volumes=False)
        self.create_machine(
            machine,
            volumes=volumes,
            ports=ports,
            legacy_machine=legacy_domain,
        )
        LOG.debug("The domain %s was recreated", machine.uuid)

    def list_storage_pools(self) -> tp.List[models.ThinStoragePool]:
        """List storage pools."""
        pools = []
        _pools = self._client.listAllStoragePools()

        # TODO(akremenetsky): Need to update information about volumes
        for p in _pools:
            pool_element = ET.fromstring(p.XMLDesc())
            pool_type = pool_element.get("type")

            pools.append(
                models.ThinStoragePool(
                    uuid=sys_uuid.UUID(p.UUIDString()),
                    name=p.name(),
                    capacity_usable=p.info()[1] >> 30,  # GB
                    available_actual=p.info()[3] >> 30,  # GB
                    pool_type=pool_type,
                )
            )

        return pools
