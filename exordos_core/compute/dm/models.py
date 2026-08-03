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

import logging
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
from restalchemy.storage import exceptions as ra_storage_exc
from restalchemy.storage.sql import orm

from exordos_core.common import constants as cc
from exordos_core.common import exceptions as ex_exceptions
from exordos_core.common import system
from exordos_core.common import utils
from exordos_core.common.dm import models as cm
from exordos_core.compute import constants as nc
from exordos_core.quota.dm.models import QuotaModelMixin

LOG = logging.getLogger(__name__)


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
    # When the interfaces are OVS-backed (the SDN br-int), plug overlay
    # ports as bridge interfaces into the OVS integration bridge with an
    # openvswitch virtualport carrying the port uuid as iface-id. Ports
    # whose source is a real libvirt network (the flat/boot networks) keep
    # the classic network path even on an ovs pool. Default off so plain
    # Linux-bridge/network pools are unchanged.
    ovs = properties.property(
        types.Boolean(),
        default=False,
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
    # Node uuid of the hypervisor host whose universal agent wires the
    # pool's guests into the SDN dataplane (the fabric lives on the
    # hypervisor). None — guests wire themselves.
    hypervisor_node = properties.property(types.AllowNone(types.UUID()), default=None)
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
    QuotaModelMixin,
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
    QuotaModelMixin,
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
        # Preserve a caller-set network pin (used by the network service to
        # place the node on a specific network when several coexist).
        pinned = (self.default_network or {}).get("network")
        self.default_network = {
            "subnet": str(port.subnet),
            "port": str(port.uuid),
            "ipv4": str(port.ipv4) if port.ipv4 else None,
            "target_ipv4": str(port.target_ipv4) if port.target_ipv4 else None,
            "mask": str(port.mask) if port.mask else None,
            "mac": port.mac,
        }
        if pinned is not None:
            self.default_network["network"] = pinned
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
        # Provision on fetch, exactly as the ua issue_key action does: a
        # node registered out of band (a hypervisor host joined via the CLI)
        # may not have had its key created yet, and `cn get-key` is the call
        # that installs it. get_or_create returns the existing key otherwise.
        enc_key = ua_models.NodeEncryptionKey.get_or_create(self.uuid)

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
    # A network is declarable from an element manifest
    # (`$core.network.networks`), and the core agent that applies those
    # resources reads the model through this mixin.
    ua_models.ResourceMixin,
):
    __tablename__ = "compute_networks"
    __driver_map__ = {}

    driver_spec = properties.property(types.Dict(), default=lambda: {})
    # Topology, not type: `access` is the tenancy boundary.
    # `private` — owner-only; `public` — visible to every project and
    # allocatable by them (publishing needs `network.network.share`).
    # (`egress` — the default-route model — is not here: DVR is the only
    # thing the data plane does, and a knob whose other value silently means
    # the same is worse than no knob.)
    access = properties.property(
        types.Enum(["private", "public"]),
        default="private",
    )

    def delete(self, session=None, **kwargs):
        # A network whose subnets are still there would take them with it in
        # the storage layer's own words — a RestrictViolation the caller sees
        # as a 500. Say what is actually in the way instead.
        subnets = Subnet.objects.get_all(filters={"network": dm_filters.EQ(self.uuid)})
        if subnets:
            raise ra_storage_exc.ConflictRecords(
                model="Network",
                msg=f"still has {len(subnets)} subnet(s); delete them first",
            )
        return super().delete(session=session, **kwargs)

    def load_driver(self) -> tp.Type["AbstractNetworkDriver"]:
        # Keyed by network uuid as well: a driver instance is bound to its
        # network, and two networks may momentarily share the same spec
        # (e.g. a bare {"driver": "ovs_evpn"} before VNI allocation) — a
        # spec-only key would hand network B a driver bound to network A.
        driver_key = "%s/%s" % (self.uuid, self.driver_spec)

        if driver_key in self.__driver_map__:
            driver = self.__driver_map__[driver_key]
            # The cache spares the entry-point lookup, not the network row:
            # the instance holds the model it was built from, so a field
            # outside the key (access) would keep the value it
            # had when the process first touched this network.
            driver.bind(self)
            return driver

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
    # Declarable from a manifest as `$core.network.subnets`; see Network.
    ua_models.ResourceMixin,
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
    # May the scheduler give a node a port here? A subnet that exists only to
    # hand out addresses — a pool of floating addresses on the management
    # segment, say — is not a place to put guests: on a libvirt pool a
    # subnet doubles as the name of the libvirt network a guest plugs into,
    # and a pool has none. Left true, so every existing subnet keeps taking
    # nodes exactly as before.
    placeable = properties.property(
        types.Boolean(),
        default=True,
    )
    # May a project that does not own this subnet take addresses out of it?
    # The narrow authorization an ingress pool needs, said about the pool
    # itself. Publishing the whole *network* is the wide one — it opens every
    # subnet of it, the management range included, to be drawn from — and it
    # was what a floating address for a realm required, because the address
    # is allocated in the port's project while the pool belongs to the
    # network's. False by default: a subnet is nobody else's to draw from
    # until it is said to be.
    shared_pool = properties.property(
        types.Boolean(),
        default=False,
    )

    def insert(self, session=None):
        # A subnet defines topology on its network, so it must cite a network
        # the caller owns — publishing a network lets others *allocate* in it,
        # not add subnets to it. Refuse a foreign (or missing) network here.
        from exordos_core.user_api.network.dm import models as net_models

        net_models.validate_reference(self.project_id, Network, self.network, "network")
        # A subnet's default group used to be refused here when the network
        # had spent its sixteen identity bits, because a subnet whose guests
        # cannot be given a default group is not a state worth reaching. It
        # is no longer that state: a group past the budget is carried as an
        # address set instead, so the subnet gets its default either way and
        # the ceiling is on what rides in the packet, not on how many
        # subnets a network may have.
        # Materialize a default gateway route and resolver on the way to
        # storage, so a DHCP-serving subnet created without them still gives
        # its guests a working default route and DNS without the manifest
        # spelling the address out. Done at insert (create) so the value is
        # stored once and every later read — including the agent's
        # reconstruction — sees the same stored truth; reconstruction paths
        # never call insert().
        self._default_gateway_services()
        self._validate_routes()
        self._validate_no_overlapping_pool()
        return super().insert(session=session)

    def update(self, session=None, force=False):
        self._validate_routes()
        self._validate_no_overlapping_pool()
        return super().update(session=session, force=force)

    def _allocatable(self) -> netaddr.IPSet:
        """The addresses this subnet may hand out.

        Its CIDR, narrowed by `ip_range` where one is set — which is what
        an operator sets it for: to keep part of a segment for something
        the platform does not allocate.
        """
        window = netaddr.IPSet([self.cidr])
        if self.ip_range_pair is not None:
            first, last = self.ip_range_pair
            window &= netaddr.IPSet(netaddr.IPRange(first, last))
        return window

    def _validate_no_overlapping_pool(self) -> None:
        """Two subnets of one network must not hand out the same address.

        Scoped to one network, and that scope is the whole correctness of
        it: subnets of *different* networks overlap on purpose here. Every
        realm's overlay is handed the same addressing precisely because
        each is its own VRF, and a check that refused that would refuse
        the design.

        Within one network there is one segment, so two subnets that can
        both hand out an address will eventually hand out the same one to
        two machines on the same wire. That is how a pool of floating
        addresses carved out of the management segment goes wrong: the
        pool is a subnet beside the management one, and unless the
        management subnet's `ip_range` is pulled back from it, its own
        allocator walks into the pool. Nothing about that failure says
        what it is — two machines simply stop being reachable.
        """
        if self.cidr is None or self.network is None:
            return
        network_ref = getattr(self.network, "uuid", self.network)
        mine = self._allocatable()
        for other in Subnet.objects.get_all(
            filters={"network": dm_filters.EQ(network_ref)}
        ):
            if str(other.uuid) == str(self.uuid) or other.cidr is None:
                continue
            clash = mine & other._allocatable()
            if clash:
                raise ex_exceptions.ValidateException(
                    err=(
                        f"subnet {self.cidr} would hand out "
                        f"{next(iter(clash))}, which subnet {other.uuid} "
                        f"({other.cidr}) also hands out; they are on one "
                        "network and so on one segment. Narrow one of them "
                        "with ip_range"
                    )
                )

    def _validate_routes(self) -> None:
        """A gateway is an address of the subnet, and there is one of it.

        Overlay subnets only. `routers` is shared with the flat networks,
        whose accepted input this work leaves alone; on an `ovs_evpn` subnet
        the list has two readers that both take it literally. The host-local
        responder hands the guest the default route's `via` as its gateway,
        and the hypervisor builds the VNI's egress namespace holding that
        same address — with the subnet's own prefix length, because that is
        the only length there is. Neither can tell a next hop that is not on
        the subnet from one that is: the namespace comes up in a network of
        its own, the guest's traffic leaves for an address nothing holds,
        and nothing anywhere says why.

        Two default routes are the same silence in a different shape. The
        guest is handed one of them and the host holds one of them, and
        until this ran they were not obliged to be the same one.
        """
        if self.cidr is None:
            return
        network = Network.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(self.network)}
        )
        if network is None:
            return
        if (network.driver_spec or {}).get("driver") != "ovs_evpn":
            return
        defaults = 0
        for route in self.routers or []:
            if not isinstance(route, dict):
                continue
            if str(route.get("to")) in ("0.0.0.0/0", "default"):
                defaults += 1
            via = route.get("via")
            if via is None:
                continue
            try:
                nexthop = netaddr.IPAddress(str(via))
            except (netaddr.AddrFormatError, ValueError):
                raise ex_exceptions.ValidateException(
                    err=f"route via {via!r} is not an address"
                )
            if nexthop not in self.cidr:
                raise ex_exceptions.ValidateException(
                    err=(
                        f"route via {nexthop} is not an address of "
                        f"{self.cidr}: a next hop is reached on the subnet it "
                        "is handed out on, so one outside it is a gateway "
                        "nothing can answer for"
                    )
                )
        if defaults > 1:
            raise ex_exceptions.ValidateException(
                err=(
                    "a subnet has one default route: its guests are handed "
                    "one gateway and their host builds one way out"
                )
            )

    def _default_gateway_services(self) -> None:
        if not self.dhcp:
            # A DHCP-less subnet (e.g. a floating-IP pool) serves no guests
            # and hands nothing out — don't reserve an address it never uses.
            return
        if self.cidr is None or self.cidr.size <= 2:
            # No room for a host gateway distinct from network/broadcast.
            return
        gateway = self.cidr[1]
        # "Not specified" means omitted or empty; an explicit route list is
        # left as the caller's own to manage. Each field defaults on its own.
        if not self.routers:
            self.routers = [{"to": netaddr.IPNetwork("0.0.0.0/0"), "via": gateway}]
        if not self.dns_servers:
            self.dns_servers = [str(gateway)]

    def delete(self, session=None, **kwargs):
        # Same as Network: name the ports that hold the subnet rather than
        # letting the foreign key surface as a storage error.
        ports = Port.objects.get_all(filters={"subnet": dm_filters.EQ(self.uuid)})
        if ports:
            raise ra_storage_exc.ConflictRecords(
                model="Subnet",
                msg=f"still has {len(ports)} port(s); delete them first",
            )
        # Tell the driver before the row goes: a port whose node was deleted
        # takes its row with it (cascade) but leaves the resource the
        # hypervisor is holding, and once the subnet is gone the reconcile
        # loop has nothing left to notice it by.
        try:
            network = Network.objects.get_one(
                filters={"uuid": dm_filters.EQ(self.network)}
            )
            network.load_driver().delete_subnet(self)
        except Exception:
            LOG.exception("Could not retire subnet %s on the data plane", self.uuid)
        return super().delete(session=session, **kwargs)

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


class PortSimpleKind(types_dynamic.AbstractKindModel, models.SimpleViewMixin):
    """Friendly port surface — references + toggles only.

    ``security_groups`` are refs to catalog SG objects (ENI-style, never inline
    rules); ``dhcp``/``dns`` are overrides that fall back to the subnet default
    when unset; ``public`` requests a floating address
    (``{floating_from}`` | ``{address}``). The compiler expands this into the
    NF substrate; the base port never grows feature fields.
    """

    KIND = "simple"

    security_groups = properties.property(
        types.TypedList(types.UUID()), default=lambda: []
    )
    # What this port *is*, as opposed to what it may do. Each group it names
    # contributes a bit to what the fabric stamps on everything the guest
    # sends, so a rule elsewhere names one group and matches that bit
    # regardless of what else the sender belongs to. Its subnet's default
    # group is always included — joining a group adds access, it does not
    # trade one kind away for another.
    # TODO(sdn): this was `identity_group` (a single uuid) earlier on this
    # branch. Nothing has shipped either spelling, so there is no migration —
    # but a stand carrying rows with the old key fails to read them (400
    # ParseError on GET). If any deployment ever gets the old field, a data
    # migration renaming it into this list has to come with the release.
    identity_groups = properties.property(
        types.TypedList(types.UUID()), default=lambda: []
    )
    # Whether this port is also in its subnet's default group. Unset means
    # yes, which is what makes "in only from my own subnet" hold for a guest
    # that declared nothing. Setting it false is how a workload says it is
    # not one of the neighbours: it is then reachable only where a rule
    # names a group it actually belongs to.
    subnet_group = properties.property(types.AllowNone(types.Boolean()), default=None)
    dhcp = properties.property(types.AllowNone(types.Boolean()), default=None)
    dns = properties.property(types.AllowNone(types.Boolean()), default=None)
    public = properties.property(types.AllowNone(types.Dict()), default=None)


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
    # Thin-port additive fields. ``config`` is the polymorphic
    # ergonomics kind and ``port_security`` the anti-spoof toggle. The legacy
    # single-address columns above stay as the primary-address compat
    # projection.
    config = properties.property(
        types_dynamic.KindModelSelectorType(
            types_dynamic.KindModelType(PortSimpleKind),
        ),
        default=lambda: PortSimpleKind(),
    )
    port_security = properties.property(types.Boolean(), default=True)

    def _validate_references(self) -> None:
        # Everything the port cites lives in another project's reach unless
        # checked: its groups and its public address.
        from exordos_core.user_api.network.dm import models as net_models

        config = self.config
        kind = getattr(config, "KIND", None)
        if kind == "simple":
            for sg in config.security_groups or []:
                net_models.validate_reference(
                    self.project_id,
                    net_models.SecurityGroup,
                    sg,
                    "security_groups",
                )
            public = config.public or {}
            if isinstance(public, dict):
                net_models.validate_reference(
                    self.project_id,
                    net_models.Address,
                    public.get("address"),
                    "public.address",
                )
                self._validate_public_address(public.get("address"))
            self._validate_identity_groups()
        self._validate_subnet_access()

    def _validate_public_address(self, address_uuid) -> None:
        """A public address is one guest's at a time.

        The address a port names is 1:1-NATed to that guest, so two ports
        naming one address is not a shared address — it is two NAT mappings
        for the same public IP on two hypervisors, and which of them the
        fabric wins with is whichever announced last. `association` is the
        fact that decides it, so it is also what has to be checked here:
        the alternative is a caller taking somebody's floating IP away by
        citing it, and finding out from a packet capture.
        """
        from exordos_core.user_api.network.dm import models as net_models

        if not address_uuid:
            return
        address = net_models.Address.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(address_uuid)}
        )
        if address is None:
            return
        if address.allocation == net_models.AddressAllocation.FREED.value:
            raise ex_exceptions.ValidateException(
                err=f"address {address_uuid} has been released back to its subnet"
            )
        if address.association is not None and str(address.association) != str(
            self.uuid
        ):
            raise ex_exceptions.ValidateException(
                err=(
                    f"address {address_uuid} is already the public address of "
                    f"port {address.association}"
                )
            )

    def _validate_identity_groups(self) -> None:
        """The groups this port claims to be in have to be its to claim.

        Alone among the port's citations this one was never checked, and it
        is the one that decides what the fabric stamps on the guest's
        packets. Two things follow from a bit being allocated **per
        network**: naming a group is naming a *number*, and the number means
        whatever the port's own network says it means. So a caller could
        create a group on a network of their own, get bit 1 there, name that
        group from a port on another network — and the port would stamp bit
        1, which on *that* network belongs to somebody else's group. Every
        rule admitting that group would then admit this guest. Nothing
        crosses tenants at the packet level; the membership is simply forged.

        Hence both checks. The project boundary, as everywhere else, and the
        network — because a group of the right project on the wrong network
        is the same forgery with fewer steps.
        """
        from exordos_core.user_api.network.dm import models as net_models

        named = list(getattr(self.config, "identity_groups", None) or [])
        if not named:
            return
        subnet_ref = getattr(self.subnet, "uuid", self.subnet)
        subnet = Subnet.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(subnet_ref)}
        )
        network_ref = (
            getattr(subnet.network, "uuid", subnet.network)
            if subnet is not None
            else None
        )
        for group_uuid in named:
            net_models.validate_reference(
                self.project_id,
                net_models.IdentityGroup,
                group_uuid,
                "identity_groups",
            )
            group = net_models.IdentityGroup.objects.get_one_or_none(
                filters={"uuid": dm_filters.EQ(group_uuid)}
            )
            if group is None or network_ref is None:
                continue
            if str(group.network) != str(network_ref):
                raise ex_exceptions.ValidateException(
                    err=(
                        f"identity_groups names {group_uuid}, which belongs to "
                        "another network — its bit means a different group here"
                    )
                )

    def is_overlay(self) -> bool:
        """Does this port belong to an overlay network?

        Answered here, in the control plane, because here is where it can
        be: the question is about the port's network, and only this side
        has one to look at. Whoever needs the answer elsewhere is sent it.
        """
        subnet_ref = getattr(self.subnet, "uuid", self.subnet)
        subnet = Subnet.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(subnet_ref)}
        )
        network_ref = getattr(
            getattr(subnet, "network", None), "uuid", None
        ) or getattr(subnet, "network", None)
        if network_ref is None:
            return False
        network = Network.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(network_ref)}
        )
        spec = getattr(network, "driver_spec", None)
        return isinstance(spec, dict) and spec.get("driver") == nc.OVERLAY_DRIVER

    def _validate_subnet_access(self) -> None:
        # A port must not plug into another project's PRIVATE overlay subnet —
        # that would drop a guest straight onto a foreign tenant's isolated
        # segment. Flat/boot subnets ride the legacy shared-metal path (reconcile
        # creates their ports with the subnet's own project, and a flat_bridge
        # network carries no overlay isolation) so they are exempt; an
        # own-project subnet always passes. Crossing into someone else's overlay
        # is allowed only when its network is published — the same rule Address
        # enforces for the IPAM ledger.
        # ``subnet`` / ``network`` may arrive as a prefetched model object
        # rather than a bare uuid depending on how the row was loaded (reconcile
        # builds ports carrying the Subnet object itself), so take the uuid.
        subnet_ref = getattr(self.subnet, "uuid", self.subnet)
        subnet = Subnet.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(subnet_ref)}
        )
        if subnet is None or str(subnet.project_id) == str(self.project_id):
            return
        network_ref = getattr(subnet.network, "uuid", subnet.network)
        network = Network.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(network_ref)}
        )
        if network is None:
            return
        if (network.driver_spec or {}).get("driver") != "ovs_evpn":
            return
        if network.access != "public":
            raise ex_exceptions.ValidateException(
                err=(
                    f"subnet {self.subnet} belongs to another project's private network"
                )
            )

    def insert(self, session=None):
        self._validate_references()
        return super().insert(session=session)

    def update(self, session=None, force=False):
        self._validate_references()
        return super().update(session=session, force=force)

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
