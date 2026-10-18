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
from gcl_sdk.agents.universal.drivers import pool as ua_pool
from gcl_sdk.infra import constants as ic
from gcl_sdk.infra.dm import models as infra_models
import netaddr
from restalchemy.common import exceptions as ra_e
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
from exordos_core.common.dm import networking as networking_types
from exordos_core.compute import constants as nc
from exordos_core.network import subnet as subnet_utils
from exordos_core.quota.dm.models import QuotaModelMixin

LOG = logging.getLogger(__name__)


if tp.TYPE_CHECKING:
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


class MachinePool(
    models.ModelWithUUID,
    models.ModelWithNameDesc,
    models.ModelWithTimestamp,
    orm.SQLStorableMixin,
    models.SimpleViewMixin,
):
    __tablename__ = "machine_pools"

    driver_spec = properties.property(
        types_dynamic.KindModelSelectorType(
            types_dynamic.KindModelType(ua_pool.LibvirtPoolDriverSpec),
            types_dynamic.KindModelType(ua_pool.ExordosLocalHyperDriverSpec),
            types_dynamic.KindModelType(ua_pool.DummyPoolDriverSpec),
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
        types.Enum([t.value for t in ua_pool.NodeType]),
        default=ua_pool.NodeType.VM.value,
    )
    status = properties.property(
        types.Enum([s.value for s in ua_pool.MachinePoolStatus]),
        default=ua_pool.MachinePoolStatus.DISABLED.value,
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
                types_dynamic.KindModelType(ua_pool.ThinStoragePool),
            ),
        ),
        default=list,
    )


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
        types.Enum([s.value for s in ua_pool.VolumeStatus]),
        default=ua_pool.VolumeStatus.NEW.value,
    )
    # Changing a disk's tier/ephemeral tier after creation would mean
    # migrating it to a different storage pool, which isn't implemented -
    # read-only makes that unsupported change fail loudly instead of
    # being silently accepted with no actual effect on where the disk
    # lives.
    tier = properties.property(
        types.Enum([s.value for s in ic.DiskTier]),
        default=ic.DiskTier.WARM.value,
        read_only=True,
    )
    ephemeral = properties.property(types.Boolean(), default=True, read_only=True)

    # Internal field for scheduling purposes
    pool = properties.property(types.AllowNone(types.UUID()), default=None)


class UnscheduledVolume(models.ModelWithUUID, orm.SQLStorableMixin):
    __tablename__ = "compute_unscheduled_volumes"

    volume = relationships.relationship(
        Volume,
        prefetch=True,
        required=True,
    )


class ImmutableDiskPolicyError(ra_e.ValidationErrorException):
    message = "Changing tier or ephemeral of an existing disk is unsupported"


def validate_disk_policy_update(old_spec, new_spec):
    def disks(spec):
        value = spec.as_simple_view() if hasattr(spec, "as_simple_view") else spec
        if value.get("kind") == "root_disk":
            return [value]
        return value.get("disks", [])

    for old, new in zip(disks(old_spec), disks(new_spec)):
        if any(
            old.get(field, default) != new.get(field, default)
            for field, default in (("tier", "WARM"), ("ephemeral", True))
        ):
            raise ImmutableDiskPolicyError()


class ImmutableDiskPolicyMixin:
    def update_dm(self, values):
        if "disk_spec" in values:
            validate_disk_policy_update(self.disk_spec, values["disk_spec"])
        super().update_dm(values)


class NodeSet(
    ImmutableDiskPolicyMixin,
    infra_models.NodeSet,
    ua_models.InstanceWithDerivativesMixin,
    QuotaModelMixin,
    models.ModelWithTags,
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

    default_network = properties.property(
        networking_types.DefaultNetworkType(template=True), default=None
    )

    @staticmethod
    def validate_network_input(value):
        if "default_network" in value:
            from exordos_core.network.node_creation import validate_default_network

            validate_default_network(value["default_network"], template=True)

    def _validate_default_network(self):
        if self.default_network is not None:
            self.validate_network_input({"default_network": self.default_network})

    def insert(self, session=None):
        self._validate_default_network()
        return super().insert(session=session)

    def update(self, session=None, force=False):
        self._validate_default_network()
        return super().update(session=session, force=force)

    def get_agents_private_keys(self):
        enc_keys = ua_models.NodeEncryptionKey.objects.get_all(
            filters={"uuid": dm_filters.In(self.nodes.keys())}
        )

        return {i.uuid: i.private_key for i in enc_keys}

    def delete(self, session=None):
        from exordos_core.network.contracts import atomic_session

        with atomic_session(self._get_engine(), session) as current:
            for node in Node.objects.get_all(
                filters={"node_set": dm_filters.EQ(self.uuid)}, session=current
            ):
                node.delete(session=current)
            return super().delete(session=current)

    def set_active(self):
        self.status = nc.NodeStatus.ACTIVE.value
        self.save()


class Node(
    ImmutableDiskPolicyMixin,
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

    @staticmethod
    def validate_network_input(value):
        if "default_network" in value:
            from exordos_core.network.node_creation import validate_default_network

            validate_default_network(value["default_network"])

    @property
    def default_network(self):
        request = self.properties.get_value("default_network")
        observed = getattr(self, "_default_network_view", None)
        if observed is not None and observed[0] == request:
            return observed[1]
        return request

    @classmethod
    def project_observations(cls, nodes, session=None):
        from exordos_core.network.views import project_nodes

        return project_nodes(nodes, session=session)

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

    def _validate_default_network(self):
        request = self.properties.get_value("default_network")
        if request is not None:
            self.validate_network_input({"default_network": request})

    def insert(self, session=None):
        self._validate_default_network()
        if self.node_type != "VM":
            return self._insert_node(session=session)
        from exordos_core.network.contracts import atomic_session
        from exordos_core.network.node_creation import create_default_port

        request = self.properties.get_value("default_network")
        with atomic_session(self._get_engine(), session) as s:
            project_id = self.project_id
            if self.node_set is not None and self.project_id == nc.NODE_SET_PROJECT:
                project_id = NodeSet.objects.get_one(
                    filters={"uuid": dm_filters.EQ(self.node_set)}, session=s
                ).project_id
            self._insert_node(session=s)
            create_default_port(self, request, project_id, s)

    def update(self, session=None, force=False):
        self.__dict__.pop("_default_network_view", None)
        self._validate_default_network()
        return super().update(session=session, force=force)

    def _insert_node(self, session=None):
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
        # Provision on fetch like the ua issue_key action: a node registered
        # out of band (a hypervisor host joined via the CLI) may have no key yet.
        enc_key = ua_models.NodeEncryptionKey.get_or_create(self.uuid)

        return enc_key.private_key

    def delete(self, session=None):
        from exordos_core.network.contracts import atomic_session

        with atomic_session(self._get_engine(), session) as current:
            for key in ua_models.NodeEncryptionKey.objects.get_all(
                filters={"uuid": dm_filters.EQ(self.uuid)}, session=current
            ):
                key.delete(session=current)
            for port in Port.objects.get_all(
                filters=dm_filters.OR(
                    {"node": dm_filters.EQ(self.uuid)},
                    {"owner_node": dm_filters.EQ(self.uuid)},
                ),
                session=current,
            ):
                port.delete(session=current, owner_node=self.uuid)
            return super().delete(session=current)

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
        types.Enum([s.value for s in ua_pool.MachineStatus]),
        default=ua_pool.MachineStatus.NEW.value,
    )
    machine_type = properties.property(
        types.Enum([t.value for t in ua_pool.NodeType]),
        default=ua_pool.NodeType.VM.value,
    )
    node = properties.property(types.AllowNone(types.UUID()), default=None)
    pool = properties.property(types.AllowNone(types.UUID()), default=None)
    boot = properties.property(
        types.Enum([b.value for b in ua_pool.BootAlternative]),
        default=ua_pool.BootAlternative.network.value,
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
        self.status = ua_pool.MachineStatus.ACTIVE.value
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
    tier = properties.property(
        types.Enum([s.value for s in ic.DiskTier]),
        default=ic.DiskTier.WARM.value,
    )
    ephemeral = properties.property(types.Boolean(), default=True)
    # Name of the storage pool (within this compute pool) the scheduler
    # placed the volume on - needed to re-locate it by name (rather than
    # by tier/ephemeral, which several pools could match) when
    # allocating extra capacity for a resize or a reused volume.
    storage_pool = properties.property(
        types.AllowNone(types.String(max_length=255)), default=None
    )
    status = properties.property(
        types.Enum([s.value for s in ua_pool.VolumeStatus]),
        default=ua_pool.VolumeStatus.NEW.value,
    )
    index = properties.property(
        types.Integer(min_value=0, max_value=4096), default=4096
    )

    def set_active(self):
        self.status = ua_pool.VolumeStatus.ACTIVE.value
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
        types.Enum([b.value for b in ua_pool.BootAlternative]),
        default=ua_pool.BootAlternative.network.value,
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
    # Declarable from a manifest as `$core.network.networks`.
    ua_models.ResourceMixin,
):
    __tablename__ = "compute_networks"
    __driver_map__ = {}

    driver_spec = properties.property(types.Dict(), default=lambda: {})
    driver_state = properties.property(types.Dict(), default=dict)
    is_default = properties.property(types.Boolean(), default=False)

    def insert(self, session=None):
        if not nc.is_overlay(self.driver_spec):
            return super().insert(session=session)
        self.validate_overlay_spec(self.driver_spec)
        from exordos_core.network.contracts import atomic_session

        with atomic_session(self._get_engine(), session) as s:
            result = super().insert(session=s)
            self.reconcile_driver_state(session=s)
            return result

    def reconcile_driver_state(self, session=None):
        from exordos_core.network.contracts import atomic_session
        from exordos_core.network.driver.evpn import CONF
        from exordos_core.network.driver.evpn import VXLAN_OVERHEAD
        from exordos_core.network.driver.evpn import EvpnVniExhausted

        with atomic_session(self._get_engine(), session) as s:
            # Serialize all VNI allocations, including the empty table case.
            s.execute("SELECT pg_advisory_xact_lock(1163415635, 1)")
            stored = Network.objects.get_one(
                filters={"uuid": dm_filters.EQ(self.uuid)}, session=s, locked=True
            )
            state = dict(stored.driver_state)
            if "vni" not in state:
                used = {
                    n.driver_state.get("vni")
                    for n in Network.objects.get_all(session=s)
                }
                vni = next(
                    (
                        v
                        for v in range(
                            CONF.evpn.vni_range_start, CONF.evpn.vni_range_end + 1
                        )
                        if v not in used
                    ),
                    None,
                )
                if vni is None:
                    raise EvpnVniExhausted(
                        start=CONF.evpn.vni_range_start, end=CONF.evpn.vni_range_end
                    )
                state["vni"] = vni
                state["rt"] = "%d:%d" % (CONF.evpn.as_number, vni)
            state["mtu"] = (
                self.driver_spec.get("mtu") or CONF.evpn.underlay_mtu - VXLAN_OVERHEAD
            )
            self.driver_state = state
            if stored.driver_state != state:
                stored.driver_state = state
                orm.SQLStorableMixin.update(stored, session=s)

    @staticmethod
    def validate_overlay_spec(spec):
        if set(spec) - {"driver", "mtu"} or spec.get("driver") != nc.OVERLAY_DRIVER:
            raise ex_exceptions.ValidateException(
                err="overlay driver_spec accepts only driver and optional mtu"
            )
        mtu = spec.get("mtu")
        if mtu is not None and (
            not isinstance(mtu, int)
            or isinstance(mtu, bool)
            or not nc.OVERLAY_MTU_RANGE[0] <= mtu <= nc.OVERLAY_MTU_RANGE[1]
        ):
            raise ex_exceptions.ValidateException(
                err="driver_spec.mtu must be null or an integer in %d..%d"
                % nc.OVERLAY_MTU_RANGE
            )

    def update(self, session=None, force=False):
        if not nc.is_overlay(self.driver_spec):
            stored = Network.objects.get_one(
                filters={"uuid": dm_filters.EQ(self.uuid)}, session=session
            )
            if (
                nc.is_overlay(stored.driver_spec)
                or stored.project_id != self.project_id
            ):
                raise ra_storage_exc.ConflictRecords(
                    model="Network", msg="driver/project_id are immutable"
                )
            return super().update(session=session, force=force)
        self.validate_overlay_spec(self.driver_spec)
        from exordos_core.network.contracts import atomic_session

        with atomic_session(self._get_engine(), session) as s:
            s.execute("SELECT pg_advisory_xact_lock(1163415635, 1)")
            stored = Network.objects.get_one(
                filters={"uuid": dm_filters.EQ(self.uuid)}, session=s, locked=True
            )
            if (
                stored.driver_spec.get("driver") != self.driver_spec.get("driver")
                or stored.project_id != self.project_id
            ):
                raise ra_storage_exc.ConflictRecords(
                    model="Network", msg="driver/project_id are immutable"
                )
            self.driver_state = stored.driver_state
            result = super().update(session=s, force=force)
            self.reconcile_driver_state(session=s)
            return result

    # The tenancy boundary. `private`: owner-only; `public`: visible to and
    # allocatable by every project (publishing needs `network.network.share`).
    access = properties.property(
        types.Enum(["private", "public"]),
        default="private",
    )

    def delete(self, session=None, **kwargs):
        from exordos_core.network.contracts import atomic_session
        from exordos_core.user_api.network.dm import models as catalog

        with atomic_session(self._get_engine(), session) as s:
            subnets = Subnet.objects.get_all(
                filters={"network": dm_filters.EQ(self.uuid)}, session=s
            )
            if subnets:
                raise ra_storage_exc.ConflictRecords(
                    model="Network", msg="still has subnets; delete them first"
                )
            if catalog.ProxyService.objects.get_all(
                filters={"network": dm_filters.EQ(self.uuid)}, session=s
            ):
                raise ra_storage_exc.ConflictRecords(
                    model="Network", msg="still has proxy services"
                )
            for nf in catalog.NetworkFunction.objects.get_all(
                filters={"owner_network": dm_filters.EQ(self.uuid)}, session=s
            ):
                nf.collect_generated(session=s)
            return super().delete(session=s, **kwargs)

    def load_driver(self) -> tp.Type["AbstractNetworkDriver"]:
        # Keyed by network uuid too: two networks may share a spec (a bare
        # {"driver": "ovs_evpn"} before VNI allocation) but not a driver.
        driver_key = "%s/%s/%s" % (self.uuid, self.driver_spec, self.driver_state)

        if driver_key in self.__driver_map__:
            driver = self.__driver_map__[driver_key]
            # Rebind: fields outside the key (access) may have changed.
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
        default=None,
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
        default=None,
    )
    next_server = properties.property(
        types.AllowNone(types.String(max_length=256)), default=None
    )
    # May the scheduler put node ports here? False for address-only subnets
    # such as a floating pool: on libvirt a subnet names the libvirt network
    # a guest plugs into, and a pool has none.
    placeable = properties.property(
        types.Boolean(),
        default=True,
    )
    # May other projects allocate addresses here? The narrow grant an ingress
    # pool needs, instead of publishing the whole network (management range
    # included).
    shared_pool = properties.property(
        types.Boolean(),
        default=False,
    )
    floating_pool = properties.property(types.Boolean(), default=False)
    default_floating_pool = properties.property(types.Boolean(), default=False)

    def insert(self, session=None):
        from exordos_core.network.contracts import atomic_session
        from exordos_core.user_api.network.dm import models as net_models

        with atomic_session(self._get_engine(), session) as current:
            net_models.validate_reference(
                self.project_id, Network, self.network, "network", session=current
            )
            Network.objects.get_one(
                filters={"uuid": dm_filters.EQ(self.network)},
                session=current,
                locked=True,
            )
            self._validate_routes()
            self._validate_no_overlapping_pool(session=current)
            return super().insert(session=current)

    def update(self, session=None, force=False):
        from exordos_core.network.contracts import atomic_session

        with atomic_session(self._get_engine(), session) as current:
            Network.objects.get_one(
                filters={"uuid": dm_filters.EQ(self.network)},
                session=current,
                locked=True,
            )
            stored = Subnet.objects.get_one(
                filters={"uuid": dm_filters.EQ(self.uuid)}, session=current, locked=True
            )
            for field in ("network", "cidr", "ip_range", "project_id"):
                before, after = getattr(stored, field), getattr(self, field)
                if str(getattr(before, "uuid", before)) != str(
                    getattr(after, "uuid", after)
                ):
                    raise ra_storage_exc.ConflictRecords(
                        model="Subnet", msg=f"{field} is immutable"
                    )
            self._validate_routes()
            self._validate_no_overlapping_pool(session=current)
            return super().update(session=current, force=force)

    def _allocatable(self) -> netaddr.IPSet:
        """The CIDR narrowed by `ip_range`, if set."""
        window = netaddr.IPSet([self.cidr])
        if self.ip_range_pair is not None:
            first, last = self.ip_range_pair
            window &= netaddr.IPSet(netaddr.IPRange(first, last))
        return window

    def _validate_no_overlapping_pool(self, session=None) -> None:
        """Two subnets of one network must not hand out the same address.

        Only within one network: different networks are separate VRFs and
        overlap on purpose (every realm overlay reuses the same addressing).
        """
        if self.default_floating_pool and (not self.floating_pool or self.placeable):
            raise ex_exceptions.ValidateException(
                err="default_floating_pool requires an address-only floating_pool"
            )
        if self.cidr is None or self.network is None:
            return
        network_ref = getattr(self.network, "uuid", self.network)
        mine = self._allocatable()
        for other in Subnet.objects.get_all(
            filters={"network": dm_filters.EQ(network_ref)}, session=session
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

        Overlay subnets only (flat input is left alone). The guest's DHCP and
        the hypervisor's egress namespace both take the default `via`
        literally, with the subnet's prefix, so an off-subnet or second
        default gateway breaks egress silently.
        """
        if self.cidr is None:
            return
        network = Network.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(self.network)}
        )
        if network is None:
            return
        if not nc.is_overlay(network.driver_spec):
            return
        if self.cidr.version != 4:
            raise ex_exceptions.ValidateException(
                err="ovs_evpn subnets require an IPv4 CIDR"
            )
        defaults = 0
        for route in subnet_utils.effective_routers(self):
            if not isinstance(route, dict):
                continue
            destination = route.get("to")
            if (
                str(destination) != "default"
                and netaddr.IPNetwork(str(destination)).version != 4
            ):
                raise ex_exceptions.ValidateException(
                    err="ovs_evpn requires IPv4 routes"
                )
            if str(destination) in ("0.0.0.0/0", "default"):
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

    def delete(self, session=None, **kwargs):
        from exordos_core.network.contracts import atomic_session
        from exordos_core.user_api.network.dm import models as net_models

        with atomic_session(self._get_engine(), session) as s:
            # Same as Network: name what is in the way.
            ports = Port.objects.get_all(
                filters={"subnet": dm_filters.EQ(self.uuid)}, session=s
            )
            if ports:
                raise ra_storage_exc.ConflictRecords(
                    model="Subnet",
                    msg=f"still has {len(ports)} port(s); delete them first",
                )
            # A reserved address is somebody's, maybe a port's floating one.
            addresses = net_models.Address.objects.get_all(
                filters={"subnet": dm_filters.EQ(self.uuid)}, session=s
            )
            if addresses:
                raise ra_storage_exc.ConflictRecords(
                    model="Subnet",
                    msg=f"still has {len(addresses)} reserved address(es); "
                    "release them first",
                )
            # Tell the driver before the row goes: cascaded port rows leave
            # resources on the hypervisor that nothing could find afterwards.
            try:
                network = Network.objects.get_one(
                    filters={"uuid": dm_filters.EQ(self.network)}
                )
                network.load_driver().delete_subnet(self)
            except Exception:
                LOG.exception("Could not retire subnet %s on the data plane", self.uuid)
            # After the driver, which collects the functions' slices by them.
            for nf in net_models.NetworkFunction.objects.get_all(
                filters={"owner_subnet": dm_filters.EQ(self.uuid)}, session=s
            ):
                nf.collect_generated(session=s)
            return super().delete(session=s, **kwargs)

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


class Port(
    cm.CustomPropertiesMixin,
    cm.ModelWithFullAsset,
    orm.SQLStorableMixin,
    ua_models.ResourceMixin,
):
    __tablename__ = "compute_ports"

    network = None
    security_groups = ()

    __custom_properties__ = {
        "security_groups": types.TypedList(types.UUID()),
        "network": types.AllowNone(types.UUID()),
    }

    def __init__(self, network=None, security_groups=(), **kwargs):
        super().__init__(**kwargs)
        self._check_custom_property_value("network", network)
        self._network_selector = network
        self.network = network
        self.security_groups = list(security_groups)

    @staticmethod
    def validate_input(value, stored=None):
        from exordos_core.network.contracts import validate_port_input

        validate_port_input(value, stored)

    @classmethod
    def project_observations(cls, ports, session=None):
        from exordos_core.network.views import project_ports

        return project_ports(ports, session=session)

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
    target_mac = properties.property(types.AllowNone(types.Mac()), default=None)
    dhcp = properties.property(types.AllowNone(types.Boolean()), default=None)
    dns = properties.property(types.AllowNone(types.Boolean()), default=None)
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
        types.Enum([s.value for s in ua_pool.PortStatus]),
        default=ua_pool.PortStatus.NEW.value,
    )
    source = properties.property(
        types.AllowNone(types.String(max_length=128)),
        default=None,
    )
    # ``port_security`` is the anti-spoof toggle. The single-address columns
    # above stay as the primary-address compat projection.
    port_security = properties.property(types.Boolean(), default=True)
    enforce_security_group = properties.property(types.Boolean(), default=False)
    # Device order; 0 is the primary interface. Unique per node.
    index = properties.property(
        types.AllowNone(types.Integer(min_value=0)),
        default=None,
    )
    owner_node = properties.property(types.AllowNone(types.UUID()), default=None)

    def _validate_references(self) -> None:
        from exordos_core.network.contracts import validate_mac

        validate_mac(self.mac)
        validate_mac(self.target_mac)
        if self.enforce_security_group and not self.is_overlay():
            raise ex_exceptions.ValidateException(
                err="enforce_security_group requires an overlay network"
            )
        subnet = Subnet.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(getattr(self.subnet, "uuid", self.subnet))}
        )
        self._validate_node_ownership()
        if subnet is not None:
            self._validate_ipv4(subnet)

    def _validate_node_ownership(self) -> None:
        node_ref = getattr(self.node, "uuid", self.node)
        if self.owner_node is not None and self.owner_node != node_ref:
            raise ra_storage_exc.ConflictRecords(
                model="Port", msg="a node-owned port cannot move to another node"
            )
        if self.node is None:
            return
        node = self.node
        if not hasattr(node, "project_id"):
            node = Node.objects.get_one_or_none(
                filters={"uuid": dm_filters.EQ(node_ref)}
            )
        if node is None:
            raise ex_exceptions.ValidateException(err=f"node {node_ref} does not exist")
        project_id = node.project_id
        if self.owner_node == node_ref:
            if node.node_type == "HW":
                return
            if node.node_set is not None and project_id == nc.NODE_SET_PROJECT:
                project_id = NodeSet.objects.get_one(
                    filters={"uuid": dm_filters.EQ(node.node_set)}
                ).project_id
        if str(project_id) != str(self.project_id):
            raise ex_exceptions.ValidateException(
                err=(
                    f"node {node_ref} belongs to another project; "
                    "a port is its node's leg and carries its project"
                )
            )

    def is_overlay(self) -> bool:
        """Does this port belong to an overlay network?

        Relations may be objects or uuids; only a bare uuid costs a query.
        """
        subnet = self.subnet
        if isinstance(subnet, (sys_uuid.UUID, str)):
            subnet = Subnet.objects.get_one_or_none(
                filters={"uuid": dm_filters.EQ(subnet)}
            )
        network = getattr(subnet, "network", None)
        if isinstance(network, (sys_uuid.UUID, str)):
            network = Network.objects.get_one_or_none(
                filters={"uuid": dm_filters.EQ(network)}
            )
        return nc.is_overlay(getattr(network, "driver_spec", None))

    def _validate_subnet_access(self, subnet) -> None:
        # No port into another project's private subnet unless its network
        # is published (as for Address). On a flat subnet the platform's own
        # legs (generated) sit in the subnet owner's network by design; a
        # hand-made one would hand ISC dhcpd an address of the caller's.
        if subnet is None:
            return
        if self.node is None:
            return
        if str(subnet.project_id) == str(self.project_id):
            return
        network_ref = getattr(subnet.network, "uuid", subnet.network)
        network = Network.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(network_ref)}
        )
        if network is None:
            return
        if network.access == "public" or subnet.shared_pool is True:
            return
        if not nc.is_overlay(network.driver_spec):
            if self.owner_node is not None:
                return
        raise ex_exceptions.ValidateException(
            err=f"subnet {self.subnet} belongs to another project's private network"
        )

    def _validate_ipv4(self, subnet) -> None:
        """A port's address is one of its subnet's, and nobody else's there."""
        if self.ipv4 is None or subnet.cidr is None:
            return
        if netaddr.IPAddress(str(self.ipv4)) not in subnet.cidr:
            raise ex_exceptions.ValidateException(
                err=f"ipv4 {self.ipv4} is not in {subnet.cidr}, the port's subnet"
            )
        taken = Port.objects.get_all(
            filters={
                "uuid": dm_filters.NE(self.uuid),
                "subnet": dm_filters.EQ(subnet.uuid),
                "ipv4": dm_filters.EQ(self.ipv4),
            },
            limit=1,
        )
        for other in taken:
            raise ex_exceptions.ValidateException(
                err=f"ipv4 {self.ipv4} is already port {other.uuid}'s"
            )

    def _assign_index(self, session):
        if self.index is not None and (
            not isinstance(self.index, int)
            or isinstance(self.index, bool)
            or self.index < 0
        ):
            raise ex_exceptions.ValidateException(
                err="index must be a non-negative integer"
            )
        if self.node is None:
            self.index = None
            return
        node_ref = getattr(self.node, "uuid", self.node)
        Node.objects.get_one(
            filters={"uuid": dm_filters.EQ(node_ref)}, session=session, locked=True
        )
        others = Port.objects.get_all(
            filters={"node": dm_filters.EQ(node_ref), "uuid": dm_filters.NE(self.uuid)},
            session=session,
        )
        taken = {p.index for p in others}
        if self.index is None:
            self.index = next(i for i in range(len(taken) + 1) if i not in taken)
        elif self.index in taken:
            raise ra_storage_exc.ConflictRecords(
                model="Port", msg=f"index {self.index} is occupied"
            )

    def insert(self, session=None):
        from exordos_core.network.contracts import atomic_session
        from exordos_core.network.contracts import resolve_subnet
        from exordos_core.user_api.network.dm import models as catalog

        with atomic_session(self._get_engine(), session) as s:
            legacy = None
            if self.subnet is None and self._network_selector is None:
                from exordos_core.network.defaults import ensure_default_subnet
                from exordos_core.network.defaults import find_legacy_flat_subnet

                legacy = find_legacy_flat_subnet(self.project_id, s)
                self.subnet = (legacy or ensure_default_subnet(self.project_id, s)).uuid
            # Node-created ports already carry validated placement/address.
            if self.subnet is None or (self.owner_node is None and legacy is None):
                subnet = resolve_subnet(
                    self.project_id,
                    self.subnet,
                    getattr(self, "_network_selector", None),
                    self.target_ipv4,
                    session=s,
                )
                self.subnet = subnet.uuid
            else:
                subnet = Subnet.objects.get_one(
                    filters={
                        "uuid": dm_filters.EQ(getattr(self.subnet, "uuid", self.subnet))
                    },
                    session=s,
                )
            # The network lock serializes MAC assignment across all its subnets.
            network_ref = getattr(subnet.network, "uuid", subnet.network)
            network = Network.objects.get_one(
                filters={"uuid": dm_filters.EQ(network_ref)}, session=s, locked=True
            )
            if (
                self.source is None
                and network.driver_spec.get("driver") == "flat_bridge"
            ):
                self.source = subnet.name
            self._validate_references()
            if legacy is None:
                self._validate_subnet_access(subnet)
            self._assign_index(s)
            Subnet.objects.get_one(
                filters={"uuid": dm_filters.EQ(subnet.uuid)}, session=s, locked=True
            )
            if self.ipv4 is None:
                allocator = catalog.Address(
                    project_id=self.project_id,
                    subnet=subnet.uuid,
                    address=str(self.target_ipv4) if self.target_ipv4 else None,
                )
                if self.target_ipv4 is not None:
                    allocator._validate_address_value(subnet)
                    allocator._validate_address_free(subnet, session=s)
                    self.ipv4 = self.target_ipv4
                else:
                    self.ipv4 = netaddr.IPAddress(
                        allocator._allocate_free_address(subnet, session=s)
                    )
            self.mask = subnet.cidr.netmask
            network_subnets = Subnet.objects.get_all(
                filters={"network": dm_filters.EQ(network_ref)}, session=s
            )
            used_macs = {
                p.mac.lower()
                for p in Port.objects.get_all(
                    filters={
                        "subnet": dm_filters.In([item.uuid for item in network_subnets])
                    },
                    session=s,
                )
                if p.mac
            }
            requested_mac = self.mac or self.target_mac
            if self.node is not None:
                used_macs.update(
                    p.mac.lower()
                    for p in Port.objects.get_all(
                        filters={
                            "node": dm_filters.EQ(getattr(self.node, "uuid", self.node))
                        },
                        session=s,
                    )
                    if p.mac
                )
            if requested_mac:
                self.mac = requested_mac.lower()
                if self.mac in used_macs:
                    raise ra_storage_exc.ConflictRecords(
                        model="Port", msg="MAC is already allocated in this network"
                    )
            else:
                self.mac = self.generate_mac()
                while self.mac in used_macs:
                    self.mac = self.generate_mac()
            result = super().insert(session=s)
            return result

    def update(self, session=None, force=False):
        from exordos_core.network.contracts import atomic_session

        with atomic_session(self._get_engine(), session) as s:
            stored = Port.objects.get_one(
                filters={"uuid": dm_filters.EQ(self.uuid)}, session=s, locked=True
            )
            old_node = getattr(stored.node, "uuid", stored.node)
            new_node = getattr(self.node, "uuid", self.node)
            for field in (
                "subnet",
                "target_ipv4",
                "target_mac",
                "project_id",
            ):
                if getattr(
                    getattr(stored, field), "uuid", getattr(stored, field)
                ) != getattr(getattr(self, field), "uuid", getattr(self, field)):
                    raise ra_storage_exc.ConflictRecords(
                        model="Port", msg=f"{field} is immutable"
                    )
            selector = getattr(self, "_network_selector", None)
            if selector is not None:
                subnet = Subnet.objects.get_one(
                    filters={"uuid": dm_filters.EQ(stored.subnet)}, session=s
                )
                if str(selector) != str(
                    getattr(subnet.network, "uuid", subnet.network)
                ):
                    raise ra_storage_exc.ConflictRecords(
                        model="Port", msg="network is immutable"
                    )
            if (
                old_node is not None
                and new_node is not None
                and str(old_node) != str(new_node)
            ):
                raise ra_storage_exc.ConflictRecords(
                    model="Port", msg="detach before moving to another node"
                )
            if (
                old_node is not None
                and old_node == new_node
                and self.index != stored.index
            ):
                raise ra_storage_exc.ConflictRecords(
                    model="Port", msg="attached index is immutable"
                )
            self._assign_index(s)
            if new_node is not None:
                peers = Port.objects.get_all(
                    filters={
                        "node": dm_filters.EQ(new_node),
                        "uuid": dm_filters.NE(self.uuid),
                    },
                    session=s,
                )
                if any(
                    peer.mac and peer.mac.lower() == self.mac.lower() for peer in peers
                ):
                    raise ra_storage_exc.ConflictRecords(
                        model="Port", msg="MAC is already attached to this node"
                    )
            if new_node is None:
                self.machine = None
                if old_node is not None:
                    self.owner_node = None
            self._validate_references()
            result = super().update(session=s, force=force)
            return result

    def delete(self, session=None, *, owner_node=None):
        from exordos_core.network.contracts import atomic_session
        from exordos_core.user_api.network.dm import models as catalog

        with atomic_session(self._get_engine(), session) as current:
            stored = Port.objects.get_one(
                filters={"uuid": dm_filters.EQ(self.uuid)}, session=current, locked=True
            )
            if owner_node is not None and stored.owner_node != owner_node:
                if getattr(stored.node, "uuid", stored.node) == owner_node:
                    stored.node = None
                    stored.machine = None
                    stored.update(session=current)
                return
            cascade = owner_node is not None
            bindings = [
                link
                for model in (catalog.PortSecurityGroup,)
                for link in model.objects.get_all(
                    filters={"port": dm_filters.EQ(self.uuid)}, session=current
                )
            ]
            addresses = catalog.Address.objects.get_all(
                filters=dm_filters.OR(
                    {"port": dm_filters.EQ(self.uuid)},
                    {"association": dm_filters.EQ(self.uuid)},
                    {"owner_port": dm_filters.EQ(self.uuid)},
                ),
                session=current,
            )
            if not cascade and (
                bindings or any(a.owner_port != self.uuid for a in addresses)
            ):
                raise ra_storage_exc.ConflictRecords(
                    model="Port", msg="still has group bindings or floating addresses"
                )
            for link in bindings:
                link.delete(session=current)
            for address in addresses:
                if address.owner_port == self.uuid:
                    address.delete(session=current, owner_port=self.uuid)
                retained = catalog.Address.objects.get_one_or_none(
                    filters={"uuid": dm_filters.EQ(address.uuid)}, session=current
                )
                if retained is not None and retained.port == self.uuid:
                    if not cascade:
                        raise ra_storage_exc.ConflictRecords(
                            model="Port", msg="still has a floating address"
                        )
                    retained.port = None
                    retained.update(session=current)
            if stored.node is not None:
                stored.node = None
                stored.machine = None
                stored.update(session=current)
            return super().delete(session=current)

    def set_active(self):
        self.status = ua_pool.PortStatus.ACTIVE.value
        self.save()

    @staticmethod
    def generate_mac(virtual_machine: bool = True) -> str:
        octets = tuple(random.randint(0, 255) for _ in range(5))

        if virtual_machine:
            return "52:54:00:%02x:%02x:%02x" % octets[2:]

        return "a9:%02x:%02x:%02x:%02x:%02x" % octets


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
