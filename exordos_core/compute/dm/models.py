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

import random
import typing as tp
import uuid as sys_uuid

from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.infra.dm import models as infra_models
import netaddr
from restalchemy.dm import filters as dm_filters
from restalchemy.dm import models
from restalchemy.dm import properties
from restalchemy.dm import relationships
from restalchemy.dm import types
from restalchemy.dm import types_dynamic
from restalchemy.dm import types_network as types_net
from restalchemy.storage.sql import orm

from exordos_core.common import constants as cc
from exordos_core.common import system
from exordos_core.common import utils
from exordos_core.common.dm import models as cm
from exordos_core.compute import constants as nc

if tp.TYPE_CHECKING:
    from exordos_core.compute.pool.drivers.base import AbstractPoolDriver
    from exordos_core.network.driver.base import AbstractNetworkDriver


class IPRange(types.BaseType):
    SEPARATOR = "-"

    def __init__(self, **kwargs):
        super(IPRange, self).__init__(openapi_type="string", **kwargs)

    def validate(self, value):
        return isinstance(value, netaddr.IPRange)

    def to_simple_type(self, value):
        return str(value)

    def from_simple_type(self, value):
        return netaddr.IPRange(*value.split(self.SEPARATOR))

    def from_unicode(self, value):
        return self.from_simple_type(value)


class AbstractStoragePool(
    models.SimpleViewMixin,
    types_dynamic.AbstractKindModel,
):
    """The abstract model for storage pool.

    This model is used to represent the storage pool and determine
    the its interfaces.
    """

    uuid = properties.property(
        types.UUID(),
        read_only=True,
        id_property=True,
        default=lambda: sys_uuid.uuid4(),
    )
    pool_type = properties.property(types.String(), required=True)

    @property
    def capacity(self) -> int:
        """Storage pool capacity."""
        return 0

    @property
    def available(self) -> int:
        """Storage pool available space."""
        return 0

    def allocate_capacity(self, size: int) -> None:
        """Allocate capacity."""
        raise NotImplementedError()

    def free_capacity(self, size: int) -> None:
        """Free capacity."""
        raise NotImplementedError()

    def has_capacity(self, size: int) -> bool:
        """Check if the storage pool has enough capacity."""
        return self.available >= size


class AbstractPoolDriverSpec(
    types_dynamic.AbstractKindModel,
    models.SimpleViewMixin,
):
    """Base class for all pool driver specs."""


class LibvirtPoolDriverSpec(AbstractPoolDriverSpec):
    KIND = "libvirt"

    connection_uri = properties.property(
        types.String(max_length=2048),
        required=True,
    )
    network = properties.property(
        types.AllowNone(types.String(max_length=255)),
        default=None,
    )
    storage_pool = properties.property(
        types.AllowNone(types.String(max_length=255)),
        default=None,
    )
    machine_prefix = properties.property(
        types.AllowNone(types.String(max_length=255)),
        default=None,
    )
    network_type = properties.property(
        types.Enum(["network", "bridge"]),
        default="network",
    )
    iface_rom_file = properties.property(
        types.AllowNone(types.String(max_length=255)),
        default=None,
    )
    iface_mtu = properties.property(
        types.Integer(min_value=0, max_value=65536),
        default=1500,
    )
    iface_source = properties.property(
        types.AllowNone(types.String(max_length=255)),
        default=None,
    )


class ExordosLocalHyperDriverSpec(LibvirtPoolDriverSpec):
    KIND = "exordos_local_hyper"

    node = properties.property(types.UUID(), required=True)


class DummyPoolDriverSpec(AbstractPoolDriverSpec):
    KIND = "dummy"


class ThinStoragePool(
    AbstractStoragePool,
    models.ModelWithNameDesc,
):
    """The model represents thin provisioned storage pool."""

    KIND = "thin_storage_pool"

    capacity_usable = properties.property(types.Integer(min_value=0), default=0)
    capacity_provisioned = properties.property(types.Integer(min_value=0), default=0)
    oversubscription_ratio = properties.property(
        types.Float(min_value=0.0), default=1.0
    )
    available_actual = properties.property(types.Integer(min_value=0), default=0)

    @property
    def capacity(self) -> int:
        """Storage pool capacity."""
        return int(self.capacity_usable * self.oversubscription_ratio)

    @property
    def available(self) -> int:
        """Storage pool available space."""
        return self.capacity - self.capacity_provisioned

    def allocate_capacity(self, size: int) -> None:
        """Allocate capacity."""
        self.capacity_provisioned += size

    def free_capacity(self, size: int) -> None:
        """Free capacity."""
        self.capacity_provisioned -= size


class MachinePool(
    models.ModelWithUUID,
    models.ModelWithNameDesc,
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
    models.SimpleViewMixin,
):
    __tablename__ = "machine_pools"
    __driver_map__ = {}

    driver_spec = properties.property(
        types_dynamic.KindModelSelectorType(
            types_dynamic.KindModelType(LibvirtPoolDriverSpec),
            types_dynamic.KindModelType(ExordosLocalHyperDriverSpec),
            types_dynamic.KindModelType(DummyPoolDriverSpec),
        ),
        required=True,
    )
    agent = properties.property(types.AllowNone(types.UUID()), default=None)
    builder = properties.property(types.AllowNone(types.UUID()), default=None)
    machine_type = properties.property(
        types.Enum([t.value for t in nc.NodeType]),
        default=nc.NodeType.VM.value,
    )
    status = properties.property(
        types.Enum([s.value for s in nc.MachinePoolStatus]),
        default=nc.MachinePoolStatus.DISABLED.value,
    )

    avail_cores = properties.property(types.Integer(), default=0)
    avail_ram = properties.property(types.Integer(), default=0)
    all_cores = properties.property(types.Integer(), default=0)
    all_ram = properties.property(types.Integer(), default=0)
    cores_ratio = properties.property(types.Float(min_value=0.0), default=1.0)
    ram_ratio = properties.property(types.Float(min_value=0.0), default=1.0)

    storage_pools = properties.property(
        types.TypedList(
            types_dynamic.KindModelSelectorType(
                types_dynamic.KindModelType(ThinStoragePool),
            ),
        ),
        default=list,
    )

    def load_driver(self) -> tp.Type["AbstractPoolDriver"]:
        """
        Load the driver for the machine pool.

        This method will try to load all drivers from the
        ``exordos_core.machine_pool_drivers`` entry point group and try to
        instantiate them with the current machine pool. If a driver is
        successfully loaded, it is stored in a cache for faster access.

        If no driver is found, a ValueError is raised.

        :return: The loaded driver class
        :raises ValueError: If no driver is found
        """
        driver_key = str(self.driver_spec)

        if driver_key in self.__driver_map__:
            return self.__driver_map__[driver_key]

        ep_group = utils.load_group_from_entry_point(nc.EP_MACHINE_POOL_DRIVERS)
        for e in ep_group:
            try:
                class_ = e.load()
                driver = class_(self)
                self.__driver_map__[driver_key] = driver
                return driver
            except Exception:
                # Just try another driver
                pass

        raise ValueError(f"Driver for spec '{self.driver_spec}' not found")


class Volume(
    infra_models.Volume,
    orm.SQLStorableMixin,
):
    __tablename__ = "node_volumes"

    uuid = properties.property(
        types.UUID(),
        read_only=True,
        id_property=True,
        default=lambda: sys_uuid.uuid4(),
    )
    status = properties.property(
        types.Enum([s.value for s in nc.VolumeStatus]),
        default=nc.VolumeStatus.NEW.value,
    )

    # Internal field for scheduling purposes
    pool = properties.property(types.AllowNone(types.UUID()), default=None)


class UnscheduledVolume(models.ModelWithUUID, orm.SQLStorableMixin):
    __tablename__ = "compute_unscheduled_volumes"

    volume = relationships.relationship(
        Volume,
        prefetch=True,
        required=True,
    )


class NodeSet(
    infra_models.NodeSet,
    ua_models.InstanceWithDerivativesMixin,
    orm.SQLStorableMixin,
):
    __tablename__ = "compute_sets"

    uuid = properties.property(
        types.UUID(),
        read_only=True,
        id_property=True,
        default=lambda: sys_uuid.uuid4(),
    )

    status = properties.property(
        types.Enum([s.value for s in nc.NodeStatus]),
        default=nc.NodeStatus.NEW.value,
    )

    def get_agents_private_keys(self):
        enc_keys = ua_models.NodeEncryptionKey.objects.get_all(
            filters={"uuid": dm_filters.In(self.nodes.keys())}
        )

        return {i.uuid: i.private_key for i in enc_keys}

    def delete(self, session=None):
        for node in Node.objects.get_all(
            filters={"node_set": dm_filters.EQ(self.uuid)},
            session=session,
        ):
            node.delete(session=session)

        super().delete(session=session)

    def set_active(self):
        self.status = nc.NodeStatus.ACTIVE.value
        self.save()


class Node(
    infra_models.Node,
    orm.SQLStorableWithJSONFieldsMixin,
):
    __tablename__ = "nodes"
    __jsonfields__ = ["default_network"]

    uuid = properties.property(
        types.UUID(),
        read_only=True,
        id_property=True,
        default=lambda: sys_uuid.uuid4(),
    )

    status = properties.property(
        types.Enum([s.value for s in nc.NodeStatus]),
        default=nc.NodeStatus.NEW.value,
    )

    node_set = properties.property(types.AllowNone(types.UUID()), default=None)

    def volumes(self) -> tp.Collection[Volume]:
        """Return the list of volumes for this node."""
        return self.disk_spec.volumes(self)

    @property
    def volume_project_id(self) -> sys_uuid.UUID:
        """Project ID to use for this node's volumes.

        Handle a special case for EM. We cannot put volumes in the same
        project as the node because the volumes are created as children
        of the node and they aren't present in the manifest. So EM
        doesn't know about the volumes.
        """
        return (
            self.project_id
            if self.project_id != cc.EM_PROJECT_ID
            else cc.EM_HIDDEN_PROJECT_ID
        )

    def update_default_network(self, port: "Port") -> None:
        self.default_network = {
            "subnet": str(port.subnet),
            "port": str(port.uuid),
            "ipv4": str(port.ipv4) if port.ipv4 else None,
            "target_ipv4": str(port.target_ipv4) if port.target_ipv4 else None,
            "mask": str(port.mask) if port.mask else None,
            "mac": port.mac,
        }
        self.update()

    def get_resource_target_fields(self) -> tp.Collection[str]:
        """Return the collection of Node target fields.

        Refer to the Resource model for more details about target fields.
        """
        return frozenset(
            (
                "uuid",
                "name",
                "cores",
                "ram",
                "node_type",
                "project_id",
                "node_set",
                "placement_policies",
                "disk_spec",
            )
        )

    def insert(self, session=None):
        super().insert(session=session)

        for policy in self.placement_policies:
            allocation = FlatPlacementPolicyAllocation(
                node=self.uuid,
                policy=policy,
            )
            allocation.insert(session=session)

        # Update or create volumes for the node
        volumes = self.disk_spec.volumes(self, project_id=self.volume_project_id)
        for sdk_volume in volumes:
            # Need to convert as they are different types (SDK vs DM)
            view = sdk_volume.dump_to_simple_view()
            volume = Volume.restore_from_simple_view(**view)
            volume.insert(session=session)

        # A key may already exist for this uuid (e.g. it's also
        # registered as a local hypervisor's node, which provisions its
        # own key the same way) - reuse it instead of conflicting.
        ua_models.NodeEncryptionKey.get_or_create(self.uuid, session=session)

    def get_agent_private_key(self):
        enc_key = ua_models.NodeEncryptionKey.objects.get_one(
            filters={"uuid": dm_filters.EQ(self.uuid)}
        )

        return enc_key.private_key

    def delete(self, session=None):
        # NOTE(akremenetsky): Perhaps it's better to add a `foreign key`
        # constraint to the `node_encryption_keys` table but not all
        # nodes present in the `nodes` table. So do cleanup here.
        keys = ua_models.NodeEncryptionKey.objects.get_all(
            filters={"uuid": dm_filters.EQ(self.uuid)},
            session=session,
        )

        for key in keys:
            key.delete(session=session)

        super().delete(session=session)

    def set_active(self):
        self.status = nc.NodeStatus.ACTIVE.value
        self.save()


class Machine(cm.ModelWithFullAsset, orm.SQLStorableMixin, models.SimpleViewMixin):
    __tablename__ = "machines"

    cores = properties.property(
        types.Integer(min_value=0, max_value=4096), required=True
    )
    ram = properties.property(types.Integer(min_value=0), required=True)
    status = properties.property(
        types.Enum([s.value for s in nc.MachineStatus]),
        default=nc.MachineStatus.NEW.value,
    )
    machine_type = properties.property(
        types.Enum([t.value for t in nc.NodeType]),
        default=nc.NodeType.VM.value,
    )
    node = properties.property(types.AllowNone(types.UUID()), default=None)
    pool = properties.property(types.AllowNone(types.UUID()), default=None)
    boot = properties.property(
        types.Enum([b.value for b in nc.BootAlternative]),
        default=nc.BootAlternative.network.value,
    )
    image = properties.property(
        types.AllowNone(types.String(max_length=255)), default=None
    )

    # UUID from the firmware of the machine
    firmware_uuid = properties.property(
        types.AllowNone(types.UUID()),
        default=None,
    )

    # TODO(akremenetsky): Use a custom type for this field
    # It's a `fact` field
    block_devices = properties.property(types.Dict(), default=dict)

    def set_active(self):
        self.status = nc.MachineStatus.ACTIVE.value
        self.save()


class MachineVolume(
    cm.ModelWithFullAsset,
    orm.SQLStorableMixin,
    models.SimpleViewMixin,
):
    __tablename__ = "compute_machine_volumes"

    pool = properties.property(types.AllowNone(types.UUID()), default=None)
    machine = properties.property(types.AllowNone(types.UUID()), default=None)
    node_volume = properties.property(types.AllowNone(types.UUID()), default=None)
    size = properties.property(types.Integer(min_value=1, max_value=1000000))
    image = properties.property(
        types.AllowNone(types.String(max_length=255)), default=None
    )
    boot = properties.property(types.Boolean(), default=True)
    label = properties.property(
        types.AllowNone(types.String(max_length=127)), default=None
    )
    # TODO(g.melikov): DON'T USE! Should be dropped.
    device_type = properties.property(types.String(max_length=64), default="")
    status = properties.property(
        types.Enum([s.value for s in nc.VolumeStatus]),
        default=nc.VolumeStatus.NEW.value,
    )
    index = properties.property(
        types.Integer(min_value=0, max_value=4096), default=4096
    )

    def set_active(self):
        self.status = nc.VolumeStatus.ACTIVE.value
        self.save()


class UnscheduledNode(models.ModelWithUUID, orm.SQLStorableMixin):
    __tablename__ = "unscheduled_nodes"

    node = relationships.relationship(
        Node,
        prefetch=True,
        required=True,
    )


class Netboot(models.ModelWithUUID, orm.SQLStorableMixin, models.SimpleViewMixin):
    __tablename__ = "netboots"

    boot = properties.property(
        types.Enum([b.value for b in nc.BootAlternative]),
        default=nc.BootAlternative.network.value,
    )


class Builder(
    models.ModelWithUUID,
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
    models.SimpleViewMixin,
):
    __tablename__ = "n_builders"

    status = properties.property(
        types.Enum([s.value for s in nc.BuilderStatus]),
        default=nc.BuilderStatus.ACTIVE.value,
    )


class MachinePoolReservations(
    models.ModelWithUUID,
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
    models.SimpleViewMixin,
):
    __tablename__ = "n_machine_pool_reservations"

    pool = properties.property(types.UUID())
    machine = properties.property(types.AllowNone(types.UUID()), default=None)
    cores = properties.property(
        types.Integer(min_value=0, max_value=4096),
        required=True,
        default=0,
    )
    ram = properties.property(types.Integer(min_value=0), required=True, default=0)


class Network(
    cm.ModelWithFullAsset,
    orm.SQLStorableMixin,
    models.SimpleViewMixin,
):
    __tablename__ = "compute_networks"
    __driver_map__ = {}

    driver_spec = properties.property(types.Dict(), default=lambda: {})

    def load_driver(self) -> tp.Type["AbstractNetworkDriver"]:
        driver_key = str(self.driver_spec)

        if driver_key in self.__driver_map__:
            return self.__driver_map__[driver_key]

        ep_group = utils.load_group_from_entry_point(nc.EP_NETWORK_DRIVERS)
        for e in ep_group:
            try:
                class_ = e.load()
                driver = class_(self)
                self.__driver_map__[driver_key] = driver
                return driver
            except Exception:
                # Just try another driver
                pass

        raise ValueError(f"Driver for spec '{self.driver_spec}' not found")


class Subnet(
    cm.ModelWithFullAsset,
    orm.SQLStorableWithJSONFieldsMixin,
    models.SimpleViewMixin,
):
    __tablename__ = "compute_subnets"
    __jsonfields__ = ["dns_servers", "routers"]

    network = properties.property(types.UUID())
    cidr = properties.property(
        types_net.Network(),
        required=True,
        read_only=True,
    )
    ip_range = properties.property(
        types.AllowNone(IPRange()),
        default=None,
    )
    dhcp = properties.property(
        types.Boolean(),
        default=True,
    )
    ip_discovery_range = properties.property(
        types.AllowNone(IPRange()),
        default=None,
    )

    dns_servers = properties.property(
        types.AllowNone(types.TypedList(types.String(min_length=1, max_length=128))),
        default=lambda: [],
    )
    routers = properties.property(
        types.AllowNone(
            types.TypedList(
                types.SchemeDict(
                    {
                        "to": types_net.Network(),
                        "via": types_net.IPAddress(),
                    }
                )
            )
        ),
        default=lambda: [],
    )
    next_server = properties.property(
        types.AllowNone(types.String(max_length=256)), default=None
    )

    def port(
        self,
        target_ipv4: tp.Optional[netaddr.IPAddress] = None,
        ipv4: tp.Optional[netaddr.IPAddress] = None,
        target_mask: tp.Optional[netaddr.IPAddress] = None,
        mask: tp.Optional[netaddr.IPAddress] = None,
        mac: tp.Optional[str] = None,
        node_uuid: tp.Optional[sys_uuid.UUID] = None,
        machine_uuid: tp.Optional[sys_uuid.UUID] = None,
        project_id: tp.Optional[str] = None,
    ) -> "Port":
        port = Port(
            subnet=self.uuid,
            target_ipv4=target_ipv4,
            target_mask=target_mask,
            node=node_uuid,
            machine=machine_uuid,
            mac=mac,
            ipv4=ipv4,
            mask=mask,
            project_id=project_id or self.project_id,
        )
        return port

    @property
    def ip_range_pair(
        self,
    ) -> tp.Optional[tp.Tuple[netaddr.IPAddress, netaddr.IPAddress]]:
        if self.ip_range is None:
            return None

        return (
            netaddr.IPAddress(self.ip_range.first),
            netaddr.IPAddress(self.ip_range.last),
        )

    @property
    def ip_discovery_range_pair(
        self,
    ) -> tp.Optional[tp.Tuple[netaddr.IPAddress, netaddr.IPAddress]]:
        if self.ip_discovery_range is None:
            return None

        return (
            netaddr.IPAddress(self.ip_discovery_range.first),
            netaddr.IPAddress(self.ip_discovery_range.last),
        )


class Port(cm.ModelWithFullAsset, orm.SQLStorableMixin, models.SimpleViewMixin):
    __tablename__ = "compute_ports"

    subnet = properties.property(types.UUID())

    node = properties.property(types.AllowNone(types.UUID()), default=None)
    machine = properties.property(types.AllowNone(types.UUID()), default=None)

    interface = properties.property(
        types.AllowNone(types.String(min_length=1, max_length=32)),
        default=None,
    )
    target_ipv4 = properties.property(
        types.AllowNone(types_net.IPAddress()),
        default=None,
    )
    target_mask = properties.property(
        types.AllowNone(types_net.IPAddress()),
        default=None,
    )
    ipv4 = properties.property(types.AllowNone(types_net.IPAddress()), default=None)
    mask = properties.property(
        types.AllowNone(types_net.IPAddress()),
        default=None,
    )
    mac = properties.property(types.AllowNone(types.Mac()), default=None)
    status = properties.property(
        types.Enum([s.value for s in nc.PortStatus]),
        default=nc.PortStatus.NEW.value,
    )
    source = properties.property(
        types.AllowNone(types.String(max_length=128)),
        default=None,
    )

    def set_active(self):
        self.status = nc.PortStatus.ACTIVE.value
        self.save()

    @staticmethod
    def generate_mac(virtual_machine: bool = True) -> str:
        octets = tuple(random.randint(0, 255) for _ in range(5))

        if virtual_machine:
            return "52:54:00:%02x:%02x:%02x" % octets[2:]

        return "a9:%02x:%02x:%02x:%02x:%02x" % octets

    @classmethod
    def from_boot_network(cls):
        # NOTE(akremenetsky): There is not SDK at the moment
        # so only single boot network is supported
        boot_subnet = Subnet.objects.get_one(
            filters={
                "next_server": dm_filters.IsNot(None),
            }
        )
        return cls(
            # The UUID is not important for port in boot network.
            # It is just a placeholder.
            uuid=sys_uuid.UUID("00000000-0000-0000-0000-000000000000"),
            project_id=cc.SERVICE_PROJECT_ID,
            name="bootnet_port",
            subnet=boot_subnet.uuid,
            source=boot_subnet.name,
            mac=Port.generate_mac(),
            status=nc.PortStatus.ACTIVE.value,
        )


class NodeWithoutPorts(Node):
    __tablename__ = "compute_nodes_without_ports"

    @classmethod
    def get_nodes(cls):
        return cls.objects.get_all()

    @classmethod
    def get_vm_nodes(cls):
        return cls.objects.get_all(
            filters={
                "node_type": dm_filters.EQ(nc.NodeType.VM.value),
            }
        )


class HWNodeWithoutPorts(models.ModelWithUUID, orm.SQLStorableMixin):
    __tablename__ = "compute_hw_nodes_without_ports"

    machine = properties.property(types.UUID())
    node = properties.property(types.UUID())
    iface = properties.property(types.UUID())

    @classmethod
    def get_nodes(cls):
        return cls.objects.get_all()


class Interface(
    models.ModelWithUUID,
    models.ModelWithNameDesc,
    models.ModelWithTimestamp,
    models.SimpleViewMixin,
    orm.SQLStorableMixin,
):
    __tablename__ = "compute_net_interfaces"

    machine = properties.property(types.UUID())
    mac = properties.property(types.Mac(), required=True)
    ipv4 = properties.property(types.AllowNone(types_net.IPAddress()), default=None)
    mask = properties.property(
        types.AllowNone(types_net.IPAddress()),
        default=None,
    )
    mtu = properties.property(types.Integer(min_value=1, max_value=65536), default=1500)

    @classmethod
    def from_system(cls) -> tp.List["Interface"]:
        ifaces = []
        system_uuid = system.system_uuid()
        for iface in system.get_ifaces():
            # TODO(akremenetsky): Support multiple IPv4 addresses for
            # an interface
            uuid = sys_uuid.uuid5(system_uuid, iface["mac"])
            ipv4 = next(iter(iface["ipv4_addresses"]), None)
            mask = next(iter(iface["masks"]), None)
            ifaces.append(
                cls(
                    uuid=uuid,
                    name=iface["name"],
                    mac=iface["mac"],
                    ipv4=ipv4,
                    mask=mask,
                    mtu=iface["mtu"],
                )
            )

        return ifaces


# Placement


class PlacementDomain(
    models.SimpleViewMixin,
    models.ModelWithUUID,
    models.ModelWithNameDesc,
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
):
    __tablename__ = "compute_placement_domains"


class PlacementZone(
    models.SimpleViewMixin,
    models.ModelWithUUID,
    models.ModelWithNameDesc,
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
):
    __tablename__ = "compute_placement_zones"

    domain = relationships.relationship(
        PlacementDomain,
        prefetch=True,
        required=True,
    )


class PlacementPolicy(
    models.SimpleViewMixin,
    cm.ModelWithFullAsset,
    orm.SQLStorableMixin,
):
    __tablename__ = "compute_placement_policies"

    domain = relationships.relationship(
        PlacementDomain,
        prefetch=True,
    )
    zone = relationships.relationship(
        PlacementZone,
        prefetch=True,
    )
    kind = properties.property(
        types.Enum([p.value for p in nc.PlacementPolicyKind]),
        required=True,
        default=nc.PlacementPolicyKind.SOFT_ANTI_AFFINITY.value,
    )


class FlatPlacementPolicyAllocation(
    models.ModelWithUUID,
    models.SimpleViewMixin,
    orm.SQLStorableMixin,
):
    __tablename__ = "compute_placement_policy_allocations"

    node = properties.property(
        types.UUID(),
        required=True,
    )
    policy = properties.property(
        types.UUID(),
        required=True,
    )


class PlacementPolicyAllocation(FlatPlacementPolicyAllocation):
    node = relationships.relationship(
        Node,
        prefetch=True,
        required=True,
    )
    policy = relationships.relationship(
        PlacementPolicy,
        prefetch=True,
        required=True,
    )
