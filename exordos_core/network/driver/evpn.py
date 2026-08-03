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

import hashlib
import ipaddress
import logging
import re
import typing as tp
import urllib.parse as urlparse
import uuid as sys_uuid

from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.agents.universal.drivers import pool as ua_pool
import netaddr
from oslo_config import cfg
from restalchemy.dm import filters as dm_filters
from restalchemy.storage import exceptions as ra_storage_exc

from exordos_core.common import constants as c
from exordos_core.compute import constants as nc
from exordos_core.compute.dm import models
from exordos_core.network import exceptions
from exordos_core.network import ipam
from exordos_core.network.driver import base
from exordos_core.network.evpn.dm import models as evpn_models
from exordos_core.vs.dm import models as vs_models

LOG = logging.getLogger(__name__)

# uuid5 namespace for the per-(host, function) slices: the same function on
# the same host always compiles to the same resource, on any core.
NF_SLICE_NS = sys_uuid.UUID("6e66736c-0000-0000-0000-000000000001")
# What the agent reports for a resource it has applied, and the only status
# this driver may copy onto a target row without further thought.
ACTIVE_STATUS = "ACTIVE"
CONF = cfg.CONF

SUBNET_KIND = "evpn_subnet"
# VXLAN encapsulation cost per nesting level (design decision 11)
VXLAN_OVERHEAD = 50
# What the stock `dhcp` function hands guests as their boot filename: the
# host-local proxy, so a VM installs over the overlay without a flat NIC.
DEFAULT_NETBOOT_URL = "http://169.254.169.254/boot/boot.ipxe"

evpn_opts = [
    cfg.IntOpt(
        "vni_range_start",
        default=10000,
        help="First VNI available for automatic allocation",
    ),
    cfg.IntOpt(
        "vni_range_end",
        default=19999,
        help="Last VNI available for automatic allocation",
    ),
    cfg.IntOpt("as_number", default=65001, help="AS number of the EVPN fabric"),
    cfg.ListOpt(
        "rr_addresses",
        default=[],
        help="Route reflector addresses the hypervisors peer with",
    ),
    cfg.IntOpt(
        "underlay_mtu",
        default=1500,
        help="Underlay MTU; overlay networks default to this minus 50",
    ),
    cfg.StrOpt(
        "rr_agent",
        default="",
        help=(
            "Agent uuid of the route reflector node. When set, the "
            "driver maintains a bgp_rr target resource for it; empty "
            "means the RR is managed outside this installation"
        ),
    ),
    cfg.ListOpt(
        "rr_peer_prefixes",
        default=[],
        help=("Underlay prefixes the RR accepts passive (dynamic) neighbors from"),
    ),
    cfg.StrOpt(
        "dns_zone_suffix",
        default="internal",
        help="Suffix for the host-local DNS internal zone (<name>.<suffix>)",
    ),
    cfg.ListOpt(
        "dns_forwarders",
        default=[],
        help="Upstream resolvers the host-local DNS responder forwards to",
    ),
    cfg.ListOpt(
        "service_addresses",
        default=[],
        help=(
            "This installation's own services an overlay guest may reach "
            "directly, as ADDRESS:PORT (an element repository, a mirror). "
            "The fabric guard blocks by address class, which cannot tell an "
            "installation's insides from a service it means to offer; "
            "naming one here says which it is. Name the port: a host "
            "answers on every address it holds, so a bare ADDRESS opens "
            "everything else bound to 0.0.0.0 on that machine — sshd first "
            "among them — and giving the service an address of its own does "
            "not change that. A bare address is accepted for the case where "
            "the whole of it is the service"
        ),
    ),
    cfg.ListOpt(
        "image_origins",
        default=[],
        help=(
            "Extra SCHEME://HOST[:PORT] an overlay guest may fetch its disk "
            "image from through its hypervisor's proxy. The installation's "
            "own artifact and boot endpoints are granted already; this is "
            "for images served from somewhere else on the management "
            "network. It is an operator's list on purpose -- what a node "
            "names as its image is its owner's to write, and a grant made "
            "from that string would let a tenant have the hypervisor fetch "
            "any internal address on their behalf"
        ),
    ),
    cfg.ListOpt(
        "proxy_apis",
        default=[],
        help=(
            "Upstreams a guest may reach through the metadata proxy that "
            "authenticate their own callers (PREFIX=UPSTREAM). Unlike "
            "the netboot grants these are relayed whole — every method, no "
            "per-caller gate — because what is behind them checks "
            "credentials itself. The ecosystem endpoint a child realm calls "
            "is the case this exists for; the egress guard drops the direct "
            "route to it, and this is the door that replaces it"
        ),
    ),
]
try:
    CONF.register_opts(evpn_opts, "evpn")
except cfg.DuplicateOptError:  # pragma: no cover
    pass

# The boot API's settings: an overlay guest must reach what that API tells it
# to fetch. They are CLI options of that service, so re-register them here to
# read the same `[boot_api]` section.
boot_api_opts = [
    cfg.StrOpt("gc_boot_api", default="", help="Boot API endpoint machines use"),
    cfg.StrOpt("kernel", default="", help="Endpoint for the netboot kernel"),
    cfg.StrOpt(
        "gc_host",
        default="core.local.genesis-core.tech",
        help="Name machines know this installation by",
    ),
]
try:
    CONF.register_opts(boot_api_opts, "boot_api")
except cfg.DuplicateOptError:  # pragma: no cover
    pass

# Orchestration and status APIs, re-registered for the same reason.
for _api, _port in (("orch_api", 11011), ("status_api", 11012)):
    try:
        CONF.register_opts(
            [cfg.IntOpt("bind_port", default=_port, help="API port")], _api
        )
    except cfg.DuplicateOptError:  # pragma: no cover
        pass


def _platform_ports() -> tp.Dict[str, str]:
    """Map each platform port to its upstream, for the hypervisor's proxy.

    An overlay guest has no route to the core: the netboot ROM needs the boot
    API and its agent needs orchestration and status. All live on the core
    named by `gc_boot_api`; the ports come from each service's settings.
    """
    boot_api = CONF.boot_api.gc_boot_api
    if not boot_api:
        return {}
    parsed = urlparse.urlparse(boot_api)
    if not parsed.hostname:
        return {}
    ports = {str(parsed.port or 80): boot_api.rstrip("/")}
    for api in ("orch_api", "status_api"):
        port = CONF[api].bind_port
        ports[str(port)] = "http://%s:%d" % (parsed.hostname, port)
    return ports


def _proxy_service_rows(network_uuid) -> tp.List[tp.Any]:
    """The network's ProxyServices, sorted so the target hash is stable."""
    from exordos_core.user_api.network.dm import models as net_api_models

    if network_uuid is None:
        return []
    try:
        rows = net_api_models.ProxyService.objects.get_all(
            filters={"network": dm_filters.EQ(network_uuid)}
        )
    except Exception:
        # A failed read is not "no doors"; don't close them all on an error.
        LOG.exception("Cannot read the proxy doors of network %s", network_uuid)
        return []
    return sorted(rows, key=lambda row: int(row.port))


def _proxy_services(network_uuid) -> tp.Dict[str, str]:
    """Port -> upstream of this network's doors, like `_platform_ports`.

    A door whose destination has no address yet is skipped until it has one.
    """
    services = {}
    for row in _proxy_service_rows(network_uuid):
        upstream = row.upstream()
        if upstream:
            services[str(row.port)] = upstream
    return services


def _proxy_service_ports(network_uuid) -> tp.List[int]:
    """Ports the doors intercept, including doors with no upstream yet.

    An intercepted port with nothing behind it refuses the connection, which
    beats letting the packet out to be dropped by the egress guard.
    """
    return sorted({int(row.port) for row in _proxy_service_rows(network_uuid)})


def _proxy_service_domains(network_uuid) -> tp.List[str]:
    """The names those doors are known by, if they are known by any."""
    return sorted(
        {
            str(row.domain).strip()
            for row in _proxy_service_rows(network_uuid)
            if row.domain and str(row.domain).strip()
        }
    )


def unreachable_origin(url: str) -> tp.Optional[str]:
    """``scheme://host[:port]`` of a URL an overlay guest cannot reach.

    That is a non-global IP literal: the fabric guard drops those, so the
    proxy must bridge them. Public origins are fetched directly, and host
    names are left to the guest's resolver.
    """
    parsed = urlparse.urlparse(url or "")
    if not parsed.scheme or not parsed.hostname:
        return None
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return None
    if address.is_global:
        return None
    return "%s://%s" % (parsed.scheme, parsed.netloc)


#: One LDH label: what a guest's name may be in the internal zone.
_DNS_LABEL = re.compile(r"[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?")


def _published_addresses() -> tp.Optional[tp.List[str]]:
    """Addresses an overlay guest may reach despite the fabric guard.

    The reserved floating addresses of floating pools plus `[evpn]
    service_addresses`. The guard blocks by address class, and a floating
    address may be private yet published. ``None`` when the ledger cannot
    be read: an unknown must not close every door.
    """
    from exordos_core.user_api.network.dm import models as net_api_models

    try:
        rows = net_api_models.Address.objects.get_all(
            filters={
                "origin": dm_filters.EQ(net_api_models.AddressOrigin.FLOATING.value),
                "allocation": dm_filters.EQ(
                    net_api_models.AddressAllocation.RESERVED.value
                ),
            }
        )
        # Only a pool's: a ledger row alone does not make an address the
        # installation's to publish.
        pools = set()
        if rows:
            pools = {
                str(subnet.uuid)
                for subnet in models.Subnet.objects.get_all(
                    filters={"uuid": dm_filters.In([str(row.subnet) for row in rows])}
                )
                if net_api_models.is_floating_pool(subnet)
            }
    except Exception:
        LOG.exception("Cannot read the published addresses of this installation")
        return None
    published = {
        str(row.address) for row in rows if row.address and str(row.subnet) in pools
    }
    for entry in CONF.evpn.service_addresses:
        # Validate here: every hypervisor would drop a typo silently.
        value = str(entry).strip()
        address, _, port = value.rpartition(":")
        if not address:
            address, port = value, ""
        try:
            ipaddress.ip_network(address, strict=False)
        except ValueError:
            LOG.warning("Ignoring an unparseable [evpn] service_address: %r", entry)
            continue
        if port and not (port.isdigit() and 0 < int(port) < 65536):
            LOG.warning("Ignoring [evpn] service_address with a bad port: %r", entry)
            continue
        if not port:
            LOG.warning(
                "[evpn] service_address %r names no port: everything bound to "
                "0.0.0.0 on that host becomes reachable from every overlay",
                entry,
            )
        published.add(value)
    return sorted(published)


#: Origins already refused, so the warning is logged once, not every pass.
_refused_image_origins: tp.Set[str] = set()


def _granted_image_origins() -> tp.Set[str]:
    """Origins the proxy may fetch disk images from.

    The installation's own artifact and boot endpoints plus `[evpn]
    image_origins`. Never derived from a node's `image`: the node owner
    writes it, and trusting it would let a tenant make the hypervisor fetch
    any management address (a privilege `ProxyService` gates behind
    `network.proxy_service.custom_destination`).
    """
    origins = set()
    for url in (
        CONF.boot_api.kernel,
        CONF.boot_api.gc_boot_api,
    ) + tuple(CONF.evpn.image_origins):
        parsed = urlparse.urlparse(str(url or "").strip())
        if parsed.scheme and parsed.netloc:
            origins.add("%s://%s" % (parsed.scheme, parsed.netloc))
    return origins


def _image_origins(subnet) -> tp.List[str]:
    """Image origins of this subnet's nodes that the proxy must bridge.

    Only origins in `_granted_image_origins` are kept; others are logged once.
    """
    from exordos_core.compute.dm import models as compute_models

    granted = _granted_image_origins()
    origins = set()
    try:
        ports = compute_models.Port.objects.get_all(
            filters={"subnet": dm_filters.EQ(subnet.uuid)}
        )
        for port in ports:
            if port.node is None:
                continue
            node = compute_models.Node.objects.get_one_or_none(
                filters={"uuid": dm_filters.EQ(port.node)}
            )
            image = getattr(getattr(node, "disk_spec", None), "image", None)
            origin = unreachable_origin(image)
            if not origin:
                continue
            if origin not in granted:
                if origin not in _refused_image_origins:
                    _refused_image_origins.add(origin)
                    LOG.warning(
                        "Node %s boots from %s, which is not an origin this "
                        "installation publishes; its guests will not be able "
                        "to fetch it. Add it to [evpn] image_origins if it is "
                        "meant to be reachable",
                        getattr(node, "uuid", None),
                        origin,
                    )
                continue
            origins.add(origin)
    except Exception:
        # Transient: the next successful pass restores the grant.
        LOG.exception("Cannot read the images of the guests of subnet %s", subnet.uuid)
        return []
    return sorted(origins)


def _origin_prefix(origin: str) -> str:
    """Proxy path for this origin, stable across hosts and restarts."""
    digest = hashlib.blake2b(origin.encode(), digest_size=4).hexdigest()
    return "/images-%s" % digest


def _default_proxy_forwards(subnet=None) -> tp.List[str]:
    """Proxy grants (PREFIX=UPSTREAM) an overlay guest needs to netboot.

    Derived from the boot API's kernel and endpoint plus the subnet's image
    origins. The boot API goes last on ``/``: it matches everything, so
    specific prefixes go first.
    """
    forwards = []
    kernel = CONF.boot_api.kernel or ""
    if "/" in kernel.rstrip("/"):
        base, _, _ = kernel.rstrip("/").rpartition("/")
        prefix = urlparse.urlparse(base).path or "/artifacts"
        forwards.append("%s=%s" % (prefix, base))
    if subnet is not None:
        for origin in _image_origins(subnet):
            forwards.append("%s=%s" % (_origin_prefix(origin), origin))
    if CONF.boot_api.gc_boot_api:
        forwards.append("/=%s" % CONF.boot_api.gc_boot_api)
    return forwards


class InvalidEvpnDriverSpec(exceptions.CGNetException):
    __template__ = "Invalid ovs_evpn network driver spec: {spec}"
    spec: dict


class EvpnVniExhausted(exceptions.CGNetException):
    __template__ = "No free VNI left in range {start}..{end}"
    start: int
    end: int


class OvsEvpnNetworkDriver(base.AbstractNetworkDriver):
    """CP driver for EVPN/VXLAN overlay networks (design decision 9).

    It never touches hosts; its output and its actual state are the same
    universal-agent target resources:

    * ``evpn_port`` (per port, on the port's node agent) — evpn_connector
      client config plus the host-local DHCP record;
    * ``evpn_host`` (per node) — gobgpd session parameters;
    * ``evpn_subnet`` (unscheduled) — the driver's own actual-state marker.

    ofport is absent: the on-host driver resolves it.
    """

    def __init__(self, network: models.Network) -> None:
        spec = network.driver_spec
        if not nc.is_overlay(spec):
            raise InvalidEvpnDriverSpec(spec=spec)
        self._network = network
        #: Memoised derived RR; only a real answer is kept, so a driver built
        #: before the core node had an address asks again.
        self._derived_rr_memo: tp.Optional[tp.Tuple[sys_uuid.UUID, str, str]] = None
        self._ensure_allocated()

    def bind(self, network: models.Network) -> None:
        # A concurrent writer holding a pre-allocation driver_spec may have
        # dropped vni/rt/mtu from the freshly loaded row.
        self._network = network
        self._ensure_allocated()

    # --- VNI/RT/MTU allocation ------------------------------------------

    #: Set once the VNI has been checked against every other network. The
    #: race closes once both racers have written; re-checking on every bind
    #: would scan all networks per network per loop.
    VNI_CONFIRMED = "vni_confirmed"

    def _ensure_allocated(self) -> None:
        spec = dict(self._network.driver_spec)
        changed = False
        if "vni" not in spec:
            spec["vni"] = self._allocate_vni()
            spec.pop(self.VNI_CONFIRMED, None)
            changed = True
        elif not spec.get(self.VNI_CONFIRMED):
            if self._vni_taken_by_another(int(spec["vni"])):
                # Concurrent creates can pick the same VNI (scan and write are
                # not one transaction); a shared VNI merges two tenants'
                # overlays, so the loser re-picks, unconfirmed.
                LOG.warning(
                    "VNI %s of network %s collides, reallocating",
                    spec["vni"],
                    self._network.uuid,
                )
                spec["vni"] = self._allocate_vni()
                spec.pop("rt", None)
            else:
                spec[self.VNI_CONFIRMED] = True
            changed = True
        if "rt" not in spec:
            spec["rt"] = "%d:%d" % (CONF.evpn.as_number, spec["vni"])
            changed = True
        if "mtu" not in spec:
            spec["mtu"] = CONF.evpn.underlay_mtu - VXLAN_OVERHEAD
            changed = True
        if changed:
            self._network.driver_spec = spec
            self._network.update()
            LOG.info(
                "Allocated evpn spec for network %s: vni=%s rt=%s mtu=%s",
                self._network.uuid,
                spec["vni"],
                spec["rt"],
                spec["mtu"],
            )

    def _vni_taken_by_another(self, vni: int) -> bool:
        """True when an older network already owns this VNI.

        Ordering by uuid is arbitrary but stable, so exactly one of the two
        racing networks decides it must move.
        """
        for net in models.Network.objects.get_all():
            if net.uuid == self._network.uuid:
                continue
            spec = net.driver_spec or {}
            if not nc.is_overlay(spec) or spec.get("vni") is None:
                continue
            if int(spec["vni"]) == vni and str(net.uuid) < str(self._network.uuid):
                return True
        return False

    @staticmethod
    def _allocate_vni() -> int:
        used = set()
        for net in models.Network.objects.get_all():
            spec = net.driver_spec or {}
            if nc.is_overlay(spec) and "vni" in spec:
                used.add(int(spec["vni"]))
        for vni in range(CONF.evpn.vni_range_start, CONF.evpn.vni_range_end + 1):
            if vni not in used:
                return vni
        raise EvpnVniExhausted(
            start=CONF.evpn.vni_range_start, end=CONF.evpn.vni_range_end
        )

    @property
    def _vni(self) -> int:
        return int(self._network.driver_spec["vni"])

    @property
    def _rt(self) -> str:
        return self._network.driver_spec["rt"]

    @property
    def _mtu(self) -> int:
        return int(self._network.driver_spec["mtu"])

    # --- target resource storage helpers --------------------------------

    @staticmethod
    def _get_resource(kind: str, uuid: sys_uuid.UUID) -> ua_models.TargetResource:
        return ua_models.TargetResource.objects.get_one(
            filters={
                "kind": dm_filters.EQ(kind),
                "uuid": dm_filters.EQ(uuid),
            }
        )

    @staticmethod
    def _list_resources(
        kind: str, master: sys_uuid.UUID
    ) -> tp.List[ua_models.TargetResource]:
        return ua_models.TargetResource.objects.get_all(
            filters={
                "kind": dm_filters.EQ(kind),
                "master": dm_filters.EQ(master),
            }
        )

    # --- subnets ---------------------------------------------------------

    def list_subnets(self) -> tp.Iterable[models.Subnet]:
        # Per-loop maintenance lives here: the driver is cached and rebound,
        # not rebuilt, so __init__ runs once.
        self._ensure_agent_offers_overlay()
        self._ensure_rr()
        self._actualize_statuses()
        subnets = []
        for res in self._list_resources(SUBNET_KIND, self._network.uuid):
            subnets.append(models.Subnet.restore_from_simple_view(**res.value))
        self._refresh_nf_slices(subnets)
        return subnets

    def _refresh_nf_slices(self, subnets: tp.List[models.Subnet]) -> None:
        """Refresh NF slices on every host with a guest on the subnet.

        Editing a function changes no port, so no port recompile carries it.
        """
        for subnet in subnets:
            agents = {
                res.agent
                for res in self._list_resources(
                    evpn_models.EvpnPort.get_resource_kind(), subnet.uuid
                )
                if res.agent is not None
            }
            if not agents:
                continue
            nfs = self._service_nfs(subnet)
            for agent in agents:
                try:
                    self._ensure_host_nfs(agent, subnet, nfs=nfs)
                except Exception:
                    LOG.exception(
                        "Could not refresh the function slices of %s on %s",
                        subnet.uuid,
                        agent,
                    )

    def create_subnet(self, subnet: models.Subnet) -> models.Subnet:
        res = ua_models.TargetResource(
            uuid=subnet.uuid,
            kind=SUBNET_KIND,
            value=subnet.dump_to_simple_view(),
            master=self._network.uuid,
        )
        res.calculate_hash()
        res.full_hash = res.hash
        res.insert()
        return subnet

    def update_subnet(self, subnet: models.Subnet) -> models.Subnet:
        res = self._get_resource(SUBNET_KIND, subnet.uuid)
        res.value = subnet.dump_to_simple_view()
        res.calculate_hash()
        res.full_hash = res.hash
        res.update()
        return subnet

    def delete_subnet(self, subnet: models.Subnet) -> None:
        agents = set()
        for port_res in self._list_resources(
            evpn_models.EvpnPort.get_resource_kind(), subnet.uuid
        ):
            agents.add(port_res.agent)
            port_res.delete()
        try:
            self._get_resource(SUBNET_KIND, subnet.uuid).delete()
        except ra_storage_exc.RecordNotFound:
            pass
        # Collect each host's slices as a per-port delete would.
        for agent in agents:
            if agent is not None:
                try:
                    self._collect_host_nfs(agent, subnet)
                except Exception:
                    LOG.exception("Could not collect the function slices of %s", agent)
                self._collect_host(agent)

    # --- ports -----------------------------------------------------------

    def list_ports(self, subnet: models.Subnet) -> tp.Iterable[models.Port]:
        ports = []
        for res in self._list_resources(
            evpn_models.EvpnPort.get_resource_kind(), subnet.uuid
        ):
            value = res.value
            ipv4 = value.get("ipv4")
            ports.append(
                models.Port(
                    uuid=res.uuid,
                    subnet=subnet.uuid,
                    node=res.agent,
                    ipv4=netaddr.IPAddress(ipv4) if ipv4 else None,
                    mask=subnet.cidr.netmask,
                    mac=value.get("mac"),
                    project_id=subnet.project_id,
                    # An emitted evpn_port means the CP side is done; wiring
                    # state flows through the facts channel.
                    status=ua_pool.PortStatus.ACTIVE.value,
                )
            )
        return ports

    def _find_subnet(self, subnet_uuid: sys_uuid.UUID) -> models.Subnet:
        """The subnet row a port compiles against, not the marker.

        The ``evpn_subnet`` marker refreshes only on cidr/next_server
        changes, so it would freeze dhcp, resolvers and routes.
        """
        stored = models.Subnet.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(subnet_uuid)}
        )
        if stored is not None:
            return stored
        res = self._get_resource(SUBNET_KIND, subnet_uuid)
        return models.Subnet.restore_from_simple_view(**res.value)

    def _dhcp_record(self, subnet: models.Subnet) -> dict:
        # dump_to_simple_view keeps the record JSON-simple (the restored
        # model carries IPNetwork/IPAddress objects).
        view = subnet.dump_to_simple_view()
        return {
            "cidr": str(subnet.cidr),
            "routers": view.get("routers") or [],
            "dns_servers": view.get("dns_servers") or [],
            "mtu": self._mtu,
        }

    def _allocate_ip(
        self, subnet: models.Subnet, target_ip: tp.Optional[netaddr.IPAddress]
    ) -> netaddr.IPAddress:
        pool = ipam.Ipam(
            {subnet: list(self.list_ports(subnet))},
            ipam.ledger_reservations([subnet]),
        )
        return pool.allocate_ip(subnet, target_ip=target_ip)

    def _build_evpn_port(
        self, port: models.Port, subnet: models.Subnet, publish: bool = True
    ) -> evpn_models.EvpnPort:
        """Compile a port into its target resource.

        ``publish`` seeds what the port compiles against (services, default
        group). The per-loop staleness check passes False and must not
        write; anything unseeded just shows up as a difference.
        """
        nfs = self._host_services(port, subnet, seed=publish)
        dhcp = self._dhcp_record(subnet)
        # The host-local DNS responder serves <name>.<suffix> (+ PTR); the
        # suffix comes with the network's `dns` slice.
        name = self._guest_name(port)
        if name:
            dhcp["name"] = name
        security_rules = self._compile_security_rules(port, subnet, seed=publish)
        fips = self._compile_fips(port, claim=publish)
        return evpn_models.EvpnPort(
            uuid=port.uuid,
            mac=port.mac,
            ipv4=str(port.ipv4) if port.ipv4 else None,
            vni=self._vni,
            imp_rt=[self._rt],
            exp_rt=[self._rt],
            dhcp=dhcp,
            nfs=nfs,
            security_rules=security_rules,
            identity=self._compile_identity(port, subnet, seed=publish),
            node=port.node,
            fips=fips,
            # Anti-spoof, enforced by the agent on br-int.
            port_security=port.port_security,
            agent_uuid=port.node,
        )

    # --- network functions ------------------------------------------------

    def _host_services(
        self, port: models.Port, subnet: models.Subnet, seed: bool = True
    ) -> tp.List[dict]:
        """Descriptors of the functions serving this port.

        DHCP is the subnet's, dns and proxy the network's; all are seeded
        objects the caller may edit (`_seed_service_nfs`). The subnet's
        ``dhcp`` flag and the ``simple`` port kind's ``dhcp``/``dns``
        toggles detach a function from the port.
        """
        config = getattr(port, "config", None)
        simple = config if getattr(config, "KIND", None) == "simple" else None

        def wanted(kind: str) -> bool:
            if simple is not None and getattr(simple, kind, None) is False:
                return False
            return subnet.dhcp if kind == "dhcp" else True

        services = []
        for nf in self._service_nfs(subnet, seed=seed):
            if not wanted(nf.kind):
                continue
            # Only the kind: config rides in the `evpn_nf` slice, so editing
            # a function does not re-hash ports.
            descriptor = {"kind": nf.kind}
            if nf.kind == "proxy":
                # Exception: door ports are per-guest intercept flows, so
                # they must be in the port's hash or a new door would never
                # be delivered. Costs a re-apply when the doors change.
                descriptor["config"] = {"ports": _proxy_service_ports(subnet.network)}
            services.append(descriptor)
        return services

    def _service_nfs(self, subnet: models.Subnet, seed: bool = True) -> tp.List[tp.Any]:
        """The subnet's and network's service functions, in a stable order."""
        from exordos_core.user_api.network.dm import models as net_api_models

        if seed:
            self._seed_service_nfs(subnet)
        found = {}
        for filters in (
            {"owner_subnet": dm_filters.EQ(subnet.uuid)},
            {"owner_network": dm_filters.EQ(subnet.network)},
        ):
            for nf in net_api_models.NetworkFunction.objects.get_all(filters=filters):
                found.setdefault(nf.kind, nf)
        return [found[kind] for kind in ("dhcp", "dns", "proxy") if kind in found]

    def _default_group_rules(self, subnet: models.Subnet) -> tp.Optional[list]:
        """What a subnet's default group permits, as this version seeds it."""
        default_identity = self._default_identity_group(subnet)
        if default_identity is None:
            return None
        return [
            # Out: anything the guest starts.
            {"direction": "egress", "protocol": "any"},
            # In: from the subnet's default group by membership, not address,
            # so a neighbour cannot spoof its way in. Replies use conntrack.
            {
                "direction": "ingress",
                "protocol": "any",
                "remote_group": str(default_identity.uuid),
            },
        ]

    def refresh_generated(self, subnet: models.Subnet) -> None:
        """Keep generated services and the default group current.

        Both derive from installation settings and would otherwise go stale
        after an upgrade. Operator-edited ones are left alone (provenance).
        """
        self._seed_service_nfs(subnet)
        self._refresh_default_group(subnet)
        self._refresh_published_hosts()

    def _refresh_published_hosts(self) -> None:
        """Refresh every host's published-address list.

        Installation-wide work in the only periodic hook the driver has: a
        floating address is bound on one host but must be reachable from
        all. `_ensure_host` writes only on a hash difference.
        """
        hosts = ua_models.TargetResource.objects.get_all(
            filters={
                "kind": dm_filters.EQ(evpn_models.EvpnHost.get_resource_kind()),
            }
        )
        for host in hosts:
            try:
                self._ensure_host(host.uuid)
            except Exception:
                LOG.exception("Cannot refresh the evpn_host resource %s", host.uuid)

    def _refresh_default_group(self, subnet: models.Subnet) -> None:
        """Bring a generated default group to the shape this version seeds."""
        nf = self._default_group_nf(subnet)
        if nf is None:
            return
        from exordos_core.user_api.network.dm import models as net_api_models

        if nf.provenance != net_api_models.NFProvenance.GENERATED.value:
            return
        wanted = self._default_group_rules(subnet)
        if wanted is None or list((nf.config or {}).get("rules") or []) == wanted:
            return
        config = dict(nf.config or {})
        config["rules"] = wanted
        nf.sync_generated(config)
        LOG.info("Refreshed the default group of subnet %s", subnet.uuid)

    def _seed_service_nfs(self, subnet: models.Subnet) -> None:
        """Seed missing dhcp/dns/proxy functions with `[evpn]` defaults.

        Seeded as objects so they are visible and editable. Existing
        generated ones are refreshed; operator-edited ones are never touched.
        """
        from exordos_core.user_api.network.dm import models as net_api_models

        wanted = {
            "dhcp": (
                "owner_subnet",
                subnet.uuid,
                {"filename": DEFAULT_NETBOOT_URL},
            ),
            "dns": (
                "owner_network",
                subnet.network,
                {
                    "forwarders": self._configured_or_core(CONF.evpn.dns_forwarders),
                    "zone_suffix": CONF.evpn.dns_zone_suffix,
                    # The platform's name and the doors' domains resolve to
                    # the serving hypervisor, the only one a guest can reach.
                    "platform_names": [CONF.boot_api.gc_host]
                    + _proxy_service_domains(subnet.network),
                },
            ),
            "proxy": (
                "owner_network",
                subnet.network,
                {
                    "forwards": _default_proxy_forwards(subnet),
                    "ports": _platform_ports(),
                    "apis": list(CONF.evpn.proxy_apis),
                    "services": _proxy_services(subnet.network),
                },
            ),
        }
        for kind, (owner_field, owner_uuid, config) in wanted.items():
            if owner_uuid is None:
                continue
            nf = net_api_models.NetworkFunction.objects.get_one_or_none(
                filters={
                    "kind": dm_filters.EQ(kind),
                    owner_field: dm_filters.EQ(owner_uuid),
                }
            )
            if nf is not None:
                # Generated functions are ours to keep current so upgrades
                # reach them; operator-edited ones are not. `proxy` cannot be
                # edited via the API, so it is always generated.
                if nf.provenance == net_api_models.NFProvenance.GENERATED.value:
                    # Not `update`: that marks the function operator-owned.
                    if dict(nf.config or {}) != config:
                        nf.sync_generated(config)
                        LOG.info(
                            "Refreshed the generated %s function of %s",
                            kind,
                            owner_uuid,
                        )
                continue
            try:
                net_api_models.NetworkFunction(
                    uuid=sys_uuid.uuid4(),
                    name="%s-%s" % (kind, owner_uuid),
                    project_id=subnet.project_id,
                    kind=kind,
                    config=config,
                    provenance=net_api_models.NFProvenance.GENERATED.value,
                    **{owner_field: owner_uuid},
                ).insert()
                LOG.info("Seeded the %s function of %s", kind, owner_uuid)
            except Exception:
                # Another compile pass may have seeded it a moment ago; the
                # next iteration reads whichever won.
                LOG.exception("Could not seed the %s function of %s", kind, owner_uuid)

    def _compile_fips(self, port: models.Port, claim: bool = True) -> list:
        """Compile the port's floating IP (`fip`).

        The ``simple`` kind's ``public`` slot: ``{address}`` reuses an
        allocated object, ``{floating_from}`` auto-allocates one. It
        resolves to a 1:1 NAT mapping ``{public: <ip>}``.

        ``claim`` is False on the comparing compile, which must not write.
        """
        addr = self._public_address(port, allocate=claim)
        if addr is None or addr.address is None:
            if claim:
                self._settle_associations(port.uuid, None)
            return []
        if claim:
            self._settle_associations(port.uuid, addr)
        return [{"public": str(addr.address)}]

    def _public_address(self, port: models.Port, allocate: bool = True):
        """The address object the port's ``public`` slot resolves to.

        ``allocate`` is False on the comparing compile, which must not write.
        """
        config = getattr(port, "config", None)
        if config is None or getattr(config, "KIND", None) != "simple":
            return None
        public = getattr(config, "public", None)
        if not public:
            return None
        from exordos_core.user_api.network.dm import models as net_api_models

        addr_uuid = public.get("address")
        if addr_uuid:
            # Reuse a caller's already-allocated public address-object.
            return net_api_models.Address.objects.get_one_or_none(
                filters={"uuid": dm_filters.EQ(addr_uuid)}
            )
        if public.get("floating_from"):
            # Allocate once from the named subnet (not network: a network
            # may have several), idempotent via owner_port only — matching
            # on origin too would re-allocate if origin was rewritten.
            addr = net_api_models.Address.objects.get_one_or_none(
                filters={"owner_port": dm_filters.EQ(port.uuid)}
            )
            if addr is not None or not allocate:
                return addr
            subnet = models.Subnet.objects.get_one_or_none(
                filters={"uuid": dm_filters.EQ(public["floating_from"])}
            )
            if subnet is None:
                return None
            addr = net_api_models.Address(
                uuid=sys_uuid.uuid4(),
                project_id=port.project_id,
                subnet=subnet.uuid,
                owner_port=port.uuid,
                origin="floating",
            )
            addr.insert()  # Address.insert auto-allocates a free IP
            return addr
        return None

    @staticmethod
    def _settle_associations(port_uuid: sys_uuid.UUID, addr) -> None:
        """Set the ledger's `association` to what this port actually uses.

        The association is what blocks re-citing or freeing an address in
        use. Releasing stale ones belongs here too: unbinding keeps the
        port, so nothing else would clear the old pointer. Publishing pass
        only; writes only what changed.
        """
        from exordos_core.user_api.network.dm import models as net_api_models

        keep = str(addr.uuid) if addr is not None else None
        try:
            for stale in net_api_models.Address.objects.get_all(
                filters={"association": dm_filters.EQ(port_uuid)}
            ):
                if str(stale.uuid) == keep:
                    continue
                stale.association = None
                stale.update()
        except Exception:
            LOG.exception("Could not release the addresses port %s left", port_uuid)
        if addr is None or str(addr.association or "") == str(port_uuid):
            return
        try:
            addr.association = port_uuid
            addr.update()
        except Exception:
            LOG.exception(
                "Could not associate address %s with port %s", addr.uuid, port_uuid
            )

    @staticmethod
    def _compile_rule(rule: dict) -> dict:
        """Lower one ruleset entry to the agent's allow-list entry.

        ``{direction, protocol, port?, remote_ip?}`` becomes
        ``{direction, proto, port?, remote?}``; ``remote`` is a destination
        for egress and a source for ingress.
        """
        compiled = {
            "direction": rule.get("direction", "egress"),
            "proto": rule.get("protocol") or "any",
        }
        if rule.get("port") is not None:
            compiled["port"] = int(rule["port"])
        group = rule.get("remote_group")
        if group is not None:
            # The agent matches the group's numeric identity. A dangling
            # reference compiles to a match-nothing rule (fail closed).
            carried = OvsEvpnNetworkDriver._identity_group(group)
            if carried is None:
                LOG.error("Rule names identity group %s, which is gone", group)
                return {"direction": compiled["direction"], "proto": "none"}
            compiled["remote_identity"] = int(carried.identity)
        remote = rule.get("remote_ip")
        if remote:
            compiled["remote"] = remote
        return compiled

    @staticmethod
    def _identity_group(group_uuid):
        """The named group, or None if it is gone."""
        from exordos_core.user_api.network.dm import models as net_api_models

        return net_api_models.IdentityGroup.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(group_uuid)}
        )

    def _compile_identity(
        self,
        port: models.Port,
        subnet: tp.Optional[models.Subnet],
        seed: bool = True,
    ) -> int:
        """The identity mark stamped on this port's egress traffic.

        One bit per group: the subnet's default (unless opted out) plus each
        named group. Zero means unidentified; no group rule matches it.
        """
        config = getattr(port, "config", None)
        mark = 0
        # Named groups add to the subnet's group; leaving it is explicit.
        if subnet is not None and getattr(config, "subnet_group", None) is not False:
            default = self._default_identity_group(subnet, seed=seed)
            if default is not None:
                mark |= int(default.identity)
        for named in getattr(config, "identity_groups", None) or []:
            group = self._identity_group(named)
            if group is not None and subnet is not None:
                # `network` may be a prefetched object or a uuid.
                network = getattr(subnet.network, "uuid", subnet.network)
                if str(group.network) != str(network):
                    # Bits are per network: a foreign group's bit would claim
                    # a membership never granted. The API refuses it too.
                    LOG.error(
                        "Port %s names identity group %s of another network, "
                        "not stamping it",
                        port.uuid,
                        named,
                    )
                    continue
            if group is None:
                # Removed out of band (the API refuses it). Fail closed, loudly.
                LOG.error(
                    "Port %s is in identity group %s, which is gone",
                    port.uuid,
                    named,
                )
                continue
            mark |= int(group.identity)
        return mark

    def _default_identity_group(self, subnet: models.Subnet, seed: bool = True):
        """The subnet's own group, seeded on first use.

        The default rule names this group rather than the subnet CIDR, so
        membership is not decided by a spoofable address.
        """
        from exordos_core.user_api.network.dm import models as net_api_models

        # Scoped by network: any project may create a group with this name.
        existing = sorted(
            net_api_models.IdentityGroup.objects.get_all(
                filters={
                    "name": dm_filters.EQ(nc.default_group_name(subnet.uuid)),
                    "network": dm_filters.EQ(subnet.network),
                }
            ),
            key=lambda group: str(group.uuid),
        )
        if existing:
            return existing[0]
        if not seed:
            # A read must not create what it came to look at.
            return None
        try:
            group = net_api_models.IdentityGroup(
                uuid=sys_uuid.uuid4(),
                name=nc.default_group_name(subnet.uuid),
                description="Default identity of subnet %s" % subnet.uuid,
                project_id=subnet.project_id,
                network=subnet.network,
                # Spent bits land in the `except` (deny-all between guests);
                # `Subnet.insert` checks up front, so only a race gets here.
                identity=net_api_models.IdentityGroup.allocate_identity(subnet.network),
            )
            group.insert()
            LOG.info("Seeded the default identity group of subnet %s", subnet.uuid)
            return group
        except Exception:
            # Two compiles can race for the same subnet; whichever lost
            # reads the winner's row on the next pass.
            LOG.exception(
                "Could not seed the default identity group of %s", subnet.uuid
            )
            return None

    @staticmethod
    def _guest_name(port: models.Port) -> tp.Optional[str]:
        """The guest's DNS name: the port's name, else its node's.

        The platform creates node ports unnamed, so the node name is needed
        for guests to appear in the internal zone at all. Only a single DNS
        label is published: a dotted name would claim names below the zone
        suffix, and anything else is not a name a resolver can answer.
        """
        name = port.name
        if not name and port.node is not None:
            node = models.Node.objects.get_one_or_none(
                filters={"uuid": dm_filters.EQ(port.node)}
            )
            name = node.name if node is not None else None
        if not name or not _DNS_LABEL.fullmatch(name):
            return None
        return name

    def _compile_security_rules(
        self,
        port: models.Port,
        subnet: tp.Optional[models.Subnet] = None,
        seed: bool = True,
    ) -> list:
        """Compile the port's security groups into the agent's allow-list.

        A group is an allow-list in both directions (AWS-SG semantics). A
        port with no group gets its subnet's default group instead, which
        attaching any group replaces.
        """
        config = getattr(port, "config", None)
        if config is None or getattr(config, "KIND", None) != "simple":
            return self._default_security_rules(subnet, seed=seed)
        sg_uuids = list(getattr(config, "security_groups", None) or [])
        if not sg_uuids:
            return self._default_security_rules(subnet, seed=seed)
        # Lazy import: the SG catalog model lives in user_api (a higher layer);
        # imported here only when a port actually references a group.
        from exordos_core.user_api.network.dm import models as net_api_models

        rules = []
        for sg_uuid in sg_uuids:
            sg = net_api_models.SecurityGroup.objects.get_one_or_none(
                filters={"uuid": dm_filters.EQ(sg_uuid)}
            )
            if sg is None:
                # Removed out of band (the API refuses it): fail closed.
                LOG.error(
                    "Port %s references missing security group %s; "
                    "keeping the port filtered",
                    port.uuid,
                    sg_uuid,
                )
                rules.extend(self.DENY_ALL)
                continue
            for rule in sg.rules or []:
                rules.append(self._compile_rule(rule))
        return rules or list(self.DENY_ALL)

    # `proto: none` matches nothing: an allow-list with no allow isolates
    # the port. Used whenever the rules cannot be determined.
    DENY_ALL = ({"direction": "egress", "proto": "none"},)

    def _default_security_rules(
        self, subnet: tp.Optional[models.Subnet], seed: bool = True
    ) -> tp.List[dict]:
        """The subnet's default group (a `splitter` NF), seeded on first use.

        Everything out, in only from the subnet's own group. When it cannot
        be produced, the port gets DENY_ALL: an empty list would mean "no
        filtering".
        """
        if subnet is None:
            return []
        nf = self._default_group_nf(subnet, seed=seed)
        if nf is None:
            LOG.error(
                "Subnet %s has no default security group and one could not be "
                "seeded; its ports compile to deny-all until it has",
                subnet.uuid,
            )
            return list(self.DENY_ALL)
        return [
            self._compile_rule(rule) for rule in (nf.config or {}).get("rules") or []
        ] or list(self.DENY_ALL)

    def _default_group_nf(self, subnet: models.Subnet, seed: bool = True):
        from exordos_core.user_api.network.dm import models as net_api_models

        existing = net_api_models.NetworkFunction.objects.get_one_or_none(
            filters={
                "kind": dm_filters.EQ("splitter"),
                "owner_subnet": dm_filters.EQ(subnet.uuid),
            }
        )
        if existing is not None or not seed:
            return existing
        rules = self._default_group_rules(subnet)
        if rules is None:
            # No identity group: don't fall back to an address-based rule.
            return None
        try:
            nf = net_api_models.NetworkFunction(
                uuid=sys_uuid.uuid4(),
                name="default-%s" % subnet.uuid,
                project_id=subnet.project_id,
                kind="splitter",
                config={"rules": rules},
                provenance=net_api_models.NFProvenance.GENERATED.value,
                owner_subnet=subnet.uuid,
            )
            nf.insert()
            LOG.info("Seeded the default security group of subnet %s", subnet.uuid)
            return nf
        except Exception:
            LOG.exception(
                "Could not seed the default security group of %s", subnet.uuid
            )
            return None

    def _wiring_agent(self, node_uuid: sys_uuid.UUID) -> tp.Optional[sys_uuid.UUID]:
        """The hypervisor agent that wires the port (``hypervisor_node``).

        None defers to a later loop: the machine is not placed yet, or its
        pool names no hypervisor (set only by `exordos compute hypervisors
        init --sdn`). The guest's own agent cannot wire it: br-int is on
        the hypervisor.
        """
        machine = models.Machine.objects.get_one_or_none(
            filters={"node": dm_filters.EQ(node_uuid)}
        )
        if machine is None or machine.pool is None:
            return None
        pool = models.MachinePool.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(machine.pool)}
        )
        if pool is not None and pool.hypervisor_node is not None:
            return pool.hypervisor_node
        LOG.error(
            "Pool %s names no hypervisor node, so the overlay port of guest %s "
            "has nobody to wire it; run `exordos compute hypervisors init "
            "--sdn --sdn-pool %s` on the host that serves this pool",
            machine.pool,
            node_uuid,
            machine.pool,
        )
        return None

    def create_port(self, port: models.Port) -> models.Port:
        if port.node is None:
            # Nothing to schedule yet; NetworkService retries next loop.
            LOG.warning("Port %s has no node, skipping", port.uuid)
            return port

        agent = self._wiring_agent(port.node)
        if agent is None:
            # Not placed yet (or no hypervisor); NetworkService retries.
            LOG.info(
                "Port %s: machine of node %s not placed yet, deferring",
                port.uuid,
                port.node,
            )
            return port

        subnet = self._find_subnet(port.subnet)
        if port.ipv4 is None:
            port.ipv4 = self._allocate_ip(subnet, port.target_ipv4)
        port.mask = subnet.cidr.netmask
        if port.mac is None:
            port.mac = models.Port.generate_mac()

        res = self._build_evpn_port(port, subnet).to_ua_resource(master=subnet.uuid)
        res.agent = agent
        res.insert()

        self._ensure_host(agent)
        self._ensure_host_nfs(agent, subnet)
        port.status = ua_pool.PortStatus.ACTIVE.value
        return port

    def update_port(self, port: models.Port) -> models.Port:
        subnet = self._find_subnet(port.subnet)
        res = self._get_resource(evpn_models.EvpnPort.get_resource_kind(), port.uuid)
        new_res = self._build_evpn_port(port, subnet).to_ua_resource(master=subnet.uuid)
        res.update_value(new_res)
        res.agent = self._wiring_agent(port.node) or res.agent
        res.update()
        if res.agent is not None:
            # The port's functions may have just been switched on or off.
            self._ensure_host(res.agent)
            self._ensure_host_nfs(res.agent, subnet)
        return port

    def port_is_stale(self, port: models.Port) -> bool:
        """True when the port's compiled resource differs from the deployed one.

        Filtering, functions and addresses live in referenced objects whose
        edits leave the port's own fields untouched.
        """
        try:
            res = self._get_resource(
                evpn_models.EvpnPort.get_resource_kind(), port.uuid
            )
        except ra_storage_exc.RecordNotFound:
            return False
        public = getattr(getattr(port, "config", None), "public", None)
        if (
            public
            and public.get("floating_from")
            and self._public_address(port, allocate=False) is None
        ):
            # The comparing compile allocates nothing, so a pool asked for
            # after the port was built compiles to no address -- the same as
            # what is deployed -- and would never be taken.
            return True
        try:
            subnet = self._find_subnet(port.subnet)
            new_res = self._build_evpn_port(port, subnet, publish=False).to_ua_resource(
                master=subnet.uuid
            )
        except Exception:
            LOG.exception("Could not compile port %s for comparison", port.uuid)
            return False
        return res.hash != new_res.hash

    def delete_port(self, port: models.Port) -> None:
        node_uuid = None
        try:
            res = self._get_resource(
                evpn_models.EvpnPort.get_resource_kind(), port.uuid
            )
            node_uuid = res.agent
            res.delete()
        except ra_storage_exc.RecordNotFound:
            pass
        self._release_owned_addresses(port.uuid)
        if node_uuid is not None:
            try:
                self._collect_host_nfs(node_uuid, self._find_subnet(port.subnet))
            except Exception:
                LOG.exception("Could not collect the function slices of %s", node_uuid)
            self._collect_host(node_uuid)

    @staticmethod
    def _release_owned_addresses(port_uuid: sys_uuid.UUID) -> None:
        """Give back what a departing port held (best-effort).

        An address it owns (``floating_from``) is deleted — Address.delete
        refuses owned rows, so this is the only path. An address it merely
        cited is the caller's (Elastic-IP shape) and is only disassociated.
        """
        from exordos_core.user_api.network.dm import models as net_api_models

        OvsEvpnNetworkDriver._settle_associations(port_uuid, None)
        try:
            for addr in net_api_models.Address.objects.get_all(
                filters={"owner_port": dm_filters.EQ(port_uuid)}
            ):
                # Clear the pointers so the model's delete guards pass.
                addr.owner_port = None
                addr.association = None
                addr.update()
                addr.delete()
        except Exception:
            LOG.exception("Could not release owned addresses of port %s", port_uuid)

    # --- per-node host resources -----------------------------------------

    def _write_resource(
        self,
        model: tp.Any,
        master: tp.Optional[sys_uuid.UUID] = None,
        update: bool = True,
    ) -> tp.Optional[str]:
        """Insert the model's target resource, or update it on a hash change.

        ``update`` False only inserts. Returns "Created" or "Updated" for
        what was written, None when nothing was.
        """
        new_res = model.to_ua_resource(master=master)
        new_res.agent = model.schedule_to_ua_agent()
        try:
            res = self._get_resource(model.get_resource_kind(), model.uuid)
        except ra_storage_exc.RecordNotFound:
            new_res.insert()
            return "Created"
        if update and res.hash != new_res.hash:
            res.update_value(new_res)
            res.update()
            return "Updated"
        return None

    def _ensure_host(self, node_uuid: sys_uuid.UUID) -> None:
        """Create or refresh the node's evpn_host resource."""
        published = _published_addresses()
        host = evpn_models.EvpnHost(
            uuid=node_uuid,
            as_number=CONF.evpn.as_number,
            rr_addresses=self._configured_or_core(CONF.evpn.rr_addresses),
            published_addresses=published or [],
            agent_uuid=node_uuid,
        )
        # An unreadable ledger only creates (a later pass fills in the
        # published list): updating would close every published door.
        written = self._write_resource(host, update=published is not None)
        if written:
            LOG.info("%s evpn_host resource for node %s", written, node_uuid)

    def _ensure_host_nfs(
        self,
        node_uuid: sys_uuid.UUID,
        subnet: models.Subnet,
        nfs: tp.Optional[tp.List[tp.Any]] = None,
    ) -> None:
        """Deliver this subnet's functions to a host, one slice each.

        A slice (uuid5 of function and host) carries the function's config,
        so editing a function moves only slices, never port hashes.
        ``nfs`` lets a caller visiting several hosts read them once.
        """
        for nf in self._service_nfs(subnet) if nfs is None else nfs:
            slice_uuid = sys_uuid.uuid5(NF_SLICE_NS, "%s:%s" % (nf.uuid, node_uuid))
            compiled = evpn_models.EvpnNf(
                uuid=slice_uuid,
                kind=nf.kind,
                vni=self._vni,
                config=dict(nf.config or {}),
                agent_uuid=node_uuid,
            )
            written = self._write_resource(compiled, master=subnet.uuid)
            if written:
                LOG.info(
                    "%s the %s slice of %s on node %s",
                    written,
                    nf.kind,
                    nf.uuid,
                    node_uuid,
                )

    def _collect_host(self, node_uuid: sys_uuid.UUID) -> None:
        """Drop the node's evpn_host once its last evpn_port is gone."""
        leftovers = ua_models.TargetResource.objects.get_all(
            filters={
                "kind": dm_filters.EQ(evpn_models.EvpnPort.get_resource_kind()),
                "agent": dm_filters.EQ(node_uuid),
            }
        )
        if leftovers:
            return
        try:
            self._get_resource(
                evpn_models.EvpnHost.get_resource_kind(), node_uuid
            ).delete()
            LOG.info("Collected evpn_host resource for node %s", node_uuid)
        except ra_storage_exc.RecordNotFound:
            pass

    def _collect_host_nfs(
        self, node_uuid: sys_uuid.UUID, subnet: models.Subnet
    ) -> None:
        """Drop function slices from a host that lost its guests.

        A slice makes the host serve the VNI, so a stale one keeps the
        network there. Subnet-owned slices go with the host's last port on
        the subnet; network-owned ones with its last port on the network.
        """
        from exordos_core.user_api.network.dm import models as net_api_models

        # Not `list_subnets`: that runs the loop's maintenance pass.
        subnets = {
            str(res.value["uuid"])
            for res in self._list_resources(SUBNET_KIND, self._network.uuid)
        }
        serving = {
            str(res.master)
            for res in ua_models.TargetResource.objects.get_all(
                filters={
                    "kind": dm_filters.EQ(evpn_models.EvpnPort.get_resource_kind()),
                    "agent": dm_filters.EQ(node_uuid),
                }
            )
        } & subnets
        # Never seed on a delete path.
        for owner_field, owner_uuid, keep in (
            ("owner_subnet", subnet.uuid, str(subnet.uuid) in serving),
            ("owner_network", subnet.network, bool(serving)),
        ):
            if keep or owner_uuid is None:
                continue
            for nf in net_api_models.NetworkFunction.objects.get_all(
                filters={owner_field: dm_filters.EQ(owner_uuid)}
            ):
                slice_uuid = sys_uuid.uuid5(NF_SLICE_NS, "%s:%s" % (nf.uuid, node_uuid))
                try:
                    self._get_resource(
                        evpn_models.EvpnNf.get_resource_kind(), slice_uuid
                    ).delete()
                    LOG.info(
                        "Collected the %s slice of %s on node %s",
                        nf.kind,
                        nf.uuid,
                        node_uuid,
                    )
                except ra_storage_exc.RecordNotFound:
                    pass

    # --- the agent this installation's overlay needs ------------------------

    #: One attempt per process: the config file is the operator's too, and
    #: retrying each loop would fight a deliberate removal with agent restarts.
    _agent_wiring_attempted = False

    def _ensure_agent_offers_overlay(self) -> None:
        """Make the node's agent offer the overlay capabilities.

        Lets an overlay be enabled without re-bootstrapping: writes the
        agent config (idempotent) and restarts the agent unit, not this one.
        The helper lives in `bootstrap.defaults`: importing
        `cmd/bootstrap.py` registers CLI options and raises
        `ArgsAlreadyParsedError` in a running service.
        """
        if type(self)._agent_wiring_attempted:
            return
        type(self)._agent_wiring_attempted = True
        try:
            from exordos_core.bootstrap import defaults as bootstrap_defaults

            bootstrap_defaults._ensure_ua_config_current(with_overlay=True)
        except Exception:
            LOG.exception(
                "Cannot offer the overlay capabilities on this node's agent; "
                "an overlay created now has no data plane until the core is "
                "next bootstrapped"
            )

    # --- route reflector ---------------------------------------------------

    def _derived_rr(self) -> tp.Optional[tp.Tuple[sys_uuid.UUID, str, str]]:
        """The route reflector on the core node, read from the database.

        Lets an overlay created on a running installation work without the
        bootstrap drop-in, which is read only at startup. `[evpn]` options
        still win: a nested realm's RR address is not derivable locally.
        Returns (agent uuid, address, peer prefix), or None until the core
        node has an address.
        """
        if self._derived_rr_memo is None:
            self._derived_rr_memo = self._read_core_node_rr()
        return self._derived_rr_memo

    @staticmethod
    def _core_ip_address() -> tp.Optional[str]:
        """The core's pinned address, as the bootstrap recorded it.

        The core node's port holds an IPAM address nothing listens on.
        None on installations bootstrapped before the value existed.
        """
        value = vs_models.Value.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(c.VALUE_CORE_IP_ADDRESS_UUID)}
        )
        return str(value.value) if value is not None and value.value else None

    def _read_core_node_rr(self) -> tp.Optional[tp.Tuple[sys_uuid.UUID, str, str]]:
        try:
            node = models.Node.objects.get_one_or_none(
                filters={"node_set": dm_filters.EQ(c.CORE_SET_UUID)}
            )
            if node is None:
                return None
            core_ip = self._core_ip_address()
            for port in models.Port.objects.get_all(
                filters={"node": dm_filters.EQ(node.uuid)}
            ):
                if port.ipv4 is None:
                    continue
                subnet = models.Subnet.objects.get_one_or_none(
                    filters={"uuid": dm_filters.EQ(port.subnet)}
                )
                if subnet is None:
                    continue
                network = models.Network.objects.get_one_or_none(
                    filters={"uuid": dm_filters.EQ(subnet.network)}
                )
                # The RR peers over the underlay, not an overlay.
                if network is None or nc.is_overlay(network.driver_spec):
                    continue
                return node.uuid, core_ip or str(port.ipv4), str(subnet.cidr)
        except Exception:
            # Unknown is not a reason to write; the next loop asks again.
            LOG.exception("Cannot tell where this installation's route reflector is")
        return None

    def _configured_or_core(self, configured: tp.List[str]) -> tp.List[str]:
        """An `[evpn]` address list, else the core's address.

        Used for the RR addresses a hypervisor's gobgpd peers with and for
        the host-local resolver's upstreams: both live on the core.
        """
        if configured:
            return list(configured)
        derived = self._derived_rr()
        return [derived[1]] if derived else []

    def _ensure_rr(self) -> None:
        """Maintain the bgp_rr resource for the configured RR agent."""
        derived = self._derived_rr()
        if CONF.evpn.rr_agent:
            rr_uuid = sys_uuid.UUID(CONF.evpn.rr_agent)
            peer_prefixes = list(CONF.evpn.rr_peer_prefixes)
        elif derived is not None:
            rr_uuid, _address, prefix = derived
            peer_prefixes = [prefix]
        else:
            return
        rr = evpn_models.BgpRr(
            uuid=rr_uuid,
            as_number=CONF.evpn.as_number,
            peer_prefixes=peer_prefixes,
            agent_uuid=rr_uuid,
        )
        written = self._write_resource(rr)
        if written:
            LOG.info("%s bgp_rr resource for agent %s", written, rr_uuid)

    # --- status ------------------------------------------------------------

    #: Kinds with no builder service, so this driver actualizes their status.
    OWNED_KINDS = (
        evpn_models.EvpnPort.get_resource_kind(),
        evpn_models.EvpnHost.get_resource_kind(),
        evpn_models.EvpnNf.get_resource_kind(),
        evpn_models.BgpRr.get_resource_kind(),
    )

    def _actualize_statuses(self) -> None:
        """Copy the agent-reported status onto the target rows of OWNED_KINDS.

        Same rule as the builders' `_actualize_resource_hash_status`: take
        ACTIVE only when hashes match (else it answers an older target);
        take any other status unconditionally. Two queries, writes on change.
        """
        actual = {
            (res.kind, res.uuid): res
            for res in ua_models.Resource.objects.get_all(
                filters={"kind": dm_filters.In(list(self.OWNED_KINDS))}
            )
        }
        host_statuses = {}
        for target in ua_models.TargetResource.objects.get_all(
            filters={"kind": dm_filters.In(list(self.OWNED_KINDS))}
        ):
            res = actual.get((target.kind, target.uuid))
            if target.kind == "evpn_host":
                host_statuses[target.uuid] = (
                    res.status
                    if res is not None
                    and res.node == target.agent == target.uuid
                    and (res.status != ACTIVE_STATUS or res.hash == target.hash)
                    else "IN_PROGRESS"
                )
            if res is None:
                continue
            status = target.status
            if (res.status == ACTIVE_STATUS and target.hash == res.hash) or (
                res.status != ACTIVE_STATUS and target.status != res.status
            ):
                status = res.status
            if status == target.status and target.full_hash == res.full_hash:
                continue
            target.status = status
            target.full_hash = res.full_hash
            target.update()
            LOG.debug("Actualized %s %s to %s", target.kind, target.uuid, target.status)

        self._actualize_hypervisor_nodes(host_statuses)

    def _actualize_hypervisor_nodes(self, statuses: dict[sys_uuid.UUID, str]) -> None:
        """Host identities have no machine; their EVPN stack supplies status."""
        if not statuses:
            return
        hypervisors = {
            pool.hypervisor_node
            for pool in models.MachinePool.objects.get_all()
            if pool.hypervisor_node in statuses
        }
        if not hypervisors:
            return
        # Provisioned HW workloads retain their machine's lifecycle.
        hypervisors -= {
            machine.node
            for machine in models.Machine.objects.get_all(
                filters={"node": dm_filters.In(hypervisors)}
            )
        }
        if not hypervisors:
            return
        for node in models.Node.objects.get_all(
            filters={
                "uuid": dm_filters.In(hypervisors),
                "node_type": dm_filters.EQ("HW"),
            }
        ):
            status = statuses[node.uuid]
            if status not in ("ACTIVE", "IN_PROGRESS", "ERROR"):
                status = "IN_PROGRESS"
            if node.status != status:
                node.status = status
                # Remove only the scheduler's obsolete diagnostic.
                if node.description == "No suitable HW machines found":
                    node.description = ""
                node.save()
