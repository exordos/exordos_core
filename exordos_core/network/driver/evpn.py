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
import typing as tp
import urllib.parse as urlparse
import uuid as sys_uuid

from gcl_sdk.agents.universal.dm import models as ua_models
import netaddr
from oslo_config import cfg
from restalchemy.dm import filters as dm_filters
from restalchemy.storage import exceptions as ra_storage_exc

from exordos_core.compute import constants as nc
from exordos_core.compute.dm import models
from exordos_core.network import exceptions
from exordos_core.network import ipam
from exordos_core.network.driver import base
from exordos_core.network.evpn.dm import models as evpn_models

LOG = logging.getLogger(__name__)

# uuid5 namespace for the per-(host, function) slices: the same function on
# the same host always compiles to the same resource, on any core.
NF_SLICE_NS = sys_uuid.UUID("6e66736c-0000-0000-0000-000000000001")
# ... and of an address set on one host, which is keyed the same way and for
# the same reason: one small resource per (group, host), so a member joining
# moves it and re-hashes no port.
SET_SLICE_NS = sys_uuid.UUID("6e657473-0000-0000-0000-000000000001")
CONF = cfg.CONF

DRIVER_NAME = "ovs_evpn"
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
        "proxy_forwards",
        default=[],
        help="Netboot/metadata proxy allowlist entries (PREFIX=UPSTREAM)",
    ),
    cfg.ListOpt(
        "proxy_apis",
        default=[],
        help=(
            "Upstreams a guest may reach through the metadata proxy that "
            "authenticate their own callers (PREFIX=UPSTREAM). Unlike "
            "proxy_forwards these are relayed whole — every method, no "
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

# The boot API's own settings, read here because what an overlay guest has to
# be able to fetch is exactly what that API tells a machine to fetch. They are
# declared as CLI options in its service and so are unknown in this process;
# the values come from the same `[boot_api]` section of the same file, and
# registering them again is what makes them readable rather than guessed.
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

# The other two doors an installed guest knocks on. Same reason as above:
# they are declared as CLI options in their own services, and a guest of an
# overlay reaches neither unless its hypervisor is told where they are.
for _api, _port in (("orch_api", 11011), ("status_api", 11012)):
    try:
        CONF.register_opts(
            [cfg.IntOpt("bind_port", default=_port, help="API port")], _api
        )
    except cfg.DuplicateOptError:  # pragma: no cover
        pass


def _platform_ports() -> tp.Dict[str, str]:
    """Which core answers on each platform port, for a guest that sees none.

    A guest of an overlay reaches the installation only through its
    hypervisor's proxy, and it does not knock on one door: the netboot ROM
    asks the boot API, and the agent that comes up afterwards asks
    orchestration and status — by the core's name, which resolves to an
    address it has no route to. Its agent restarted for ever and said only
    that it could not connect.

    They are all the same core, so the address comes from the one endpoint
    already configured for machines to use, and the ports from the services'
    own settings. A whole port maps to a whole API: the guest addresses it
    by path exactly as it would directly.
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


def unreachable_origin(url: str) -> tp.Optional[str]:
    """``scheme://host[:port]`` of a URL an overlay guest cannot reach.

    "Cannot reach" is decided by the address and not by a list: the fabric
    guard drops everything a guest addresses to the space that is not
    globally routable, which is exactly the space its hypervisor's proxy
    exists to bridge. A public origin is left out on purpose — the guest
    fetches it directly, and funnelling a multi-gigabyte image through the
    proxy for nothing is a cost, not a safety.

    A host that is a name rather than an address is left out too: what it
    resolves to is the guest's resolver's answer, not ours to assume.
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


def _published_addresses() -> tp.Optional[tp.List[str]]:
    """The addresses this installation has published, from its own ledger.

    Derived, not configured — the same reason `_image_origins` is: the
    answer is already written down. A floating address is allocated with
    ``origin=floating``, and a freed row is a receipt rather than a
    reservation, so the reserved ones are exactly the doors that exist
    right now. An operator asked to list them again would be listing them
    wrong the day one moves.

    Why they have to be said at all: the fabric guard blocks by address
    class, which on an installation with a single public address no longer
    separates its insides from what it publishes. A floating address is
    private by circumstance and a front door by role, and the guard cannot
    tell the two apart without being told.

    Joined with `[evpn] service_addresses`, which says the same thing about
    the installation's own services — a repository, a mirror — that have no
    row in the ledger because they are not addresses the platform hands
    out. Both answer one question ("may an overlay guest reach this?"), so
    they arrive as one list.

    ``None`` when the ledger could not be read — an unknown, never an
    empty list: closing every door in the installation because one query
    failed is worse than leaving them as they were.
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
    except Exception:
        LOG.exception("Cannot read the published addresses of this installation")
        return None
    published = {str(row.address) for row in rows if row.address}
    for entry in CONF.evpn.service_addresses:
        # Configured by hand, so it is checked here rather than on the host:
        # a typo would otherwise travel to every hypervisor and be dropped
        # by each of them without a word.
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


def _image_origins(subnet) -> tp.List[str]:
    """The origins the guests of this subnet fetch their images from.

    Read from the nodes rather than configured, because the answer is
    already written down: a node names the image it boots, and a guest of
    an overlay can reach it only through the proxy. Until this was derived,
    an operator had to grant the repository by hand — and the symptom of
    forgetting is a guest that netboots, is told where its image is, and
    times out fetching it with "Flashing progress: 0%" on its console.
    """
    from exordos_core.compute.dm import models as compute_models

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
            if origin:
                origins.add(origin)
    except Exception:
        # A query that failed says nothing about which images this network's
        # guests boot, and reading it as "none" for good would leave the
        # grant missing for the life of the function. The pass that succeeds
        # puts it back — the platform's own functions are refreshed whenever
        # what they should say differs from what they say.
        LOG.exception("Cannot read the images of the guests of subnet %s", subnet.uuid)
        return []
    return sorted(origins)


def _origin_prefix(origin: str) -> str:
    """A stable path this origin is reached at through the proxy.

    Derived from the origin so it is the same on every host and across
    restarts — the guest is handed URLs under it, and a prefix that moved
    would strand whatever was already told to use the old one.
    """
    digest = hashlib.blake2b(origin.encode(), digest_size=4).hexdigest()
    return "/images-%s" % digest


def _default_proxy_forwards(subnet=None) -> tp.List[str]:
    """What an overlay guest must be able to fetch, and from where.

    A guest of an overlay has no route to the installation's boot network,
    and the boot API answers there. Everything it needs is therefore reached
    through its hypervisor's proxy — which is only possible if the proxy has
    been granted those upstreams, and until now nothing granted anything:
    `[evpn] proxy_forwards` was read here and set by no bootstrap, no
    manifest and no installer, so netboot over an overlay was a 404 by
    construction.

    Derived rather than configured, because the answer is already known: it
    is where the boot API tells a machine to fetch its script, its kernel and
    its initrd, plus wherever the network's nodes take their images from
    (`_image_origins`). An operator who sets `proxy_forwards` explicitly
    keeps it — the same rule the seeded functions follow everywhere else.

    The boot API lands on the proxy's root, and therefore last: a grant of
    ``/`` matches every request, so anything more specific has to be offered
    before it. The script the boot API returns names itself in the kernel
    command line (`gc_boot_api=`), and the guest has to be able to follow
    that too, not just the paths under it.
    """
    if CONF.evpn.proxy_forwards:
        return list(CONF.evpn.proxy_forwards)
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

    A pure computer: it never touches hosts. Its actual state and its
    output are the same thing — universal-agent target resources:

    * ``evpn_port``  (per port, scheduled to the port's node agent) —
      client config for evpn_connector plus the host-local DHCP record;
    * ``evpn_host``  (per node) — gobgpd session parameters;
    * ``evpn_subnet`` (unscheduled) — the driver's own actual-state
      marker consumed by nobody.

    ofport is deliberately absent: it is a host-side fact the on-host
    driver resolves itself.
    """

    def __init__(self, network: models.Network) -> None:
        spec = network.driver_spec
        if spec.get("driver") != DRIVER_NAME:
            raise InvalidEvpnDriverSpec(spec=spec)
        self._network = network
        self._ensure_allocated()

    def bind(self, network: models.Network) -> None:
        # Re-allocate as on construction: the row just loaded may have lost
        # the spec this driver depends on (a concurrent writer that carried a
        # pre-allocation copy of driver_spec), and a driver whose network has
        # no vni/rt/mtu cannot compile anything.
        self._network = network
        self._ensure_allocated()

    # --- VNI/RT/MTU allocation ------------------------------------------

    #: Set on a spec whose VNI has been checked against every other network
    #: once. The check exists for a race window that closes as soon as both
    #: racers have written; re-running it for the life of the installation
    #: made every driver bind scan the whole network table — on every
    #: reconcile loop, for every network, which is quadratic in the number of
    #: networks and buys nothing after the first pass.
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
                # Two networks created concurrently can pick the same free
                # VNI (the scan and the write are not one transaction). The
                # loser of the race re-picks: one shared VNI would merge two
                # tenants' overlays, so converging late beats staying wrong.
                # Left unconfirmed, so the new pick is checked in turn.
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
            if spec.get("driver") != DRIVER_NAME or spec.get("vni") is None:
                continue
            if int(spec["vni"]) == vni and str(net.uuid) < str(self._network.uuid):
                return True
        return False

    @staticmethod
    def _allocate_vni() -> int:
        used = set()
        for net in models.Network.objects.get_all():
            spec = net.driver_spec or {}
            if spec.get("driver") == DRIVER_NAME and "vni" in spec:
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
        # Called on every reconcile loop; driver instances are cached by
        # spec, so per-loop maintenance (the RR resource) lives here
        # rather than in __init__.
        self._ensure_rr()
        subnets = []
        for res in self._list_resources(SUBNET_KIND, self._network.uuid):
            subnets.append(models.Subnet.restore_from_simple_view(**res.value))
        self._refresh_nf_slices(subnets)
        return subnets

    def _refresh_nf_slices(self, subnets: tp.List[models.Subnet]) -> None:
        """Carry an edited function to the hosts already serving its guests.

        A function is not a port: editing a network's resolver changes
        nothing about any port, so nothing would recompile one — and
        nothing should. The slices are refreshed here instead, on the loop
        that already runs, for every host that has a guest on the subnet.
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
            self._collect_generated_nfs(port_res.uuid)
            port_res.delete()
        try:
            self._get_resource(SUBNET_KIND, subnet.uuid).delete()
        except ra_storage_exc.RecordNotFound:
            pass
        # Dropping a whole subnet retires its ports in one go; each of their
        # hosts still needs collecting, exactly as a per-port delete would.
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
                    # An emitted evpn_port means the CP side is complete —
                    # machine creation gates on an ACTIVE port (the actual
                    # wiring state flows through the facts channel).
                    status=nc.PortStatus.ACTIVE.value,
                )
            )
        return ports

    def _find_subnet(self, subnet_uuid: sys_uuid.UUID) -> models.Subnet:
        """The subnet a port compiles against — the row, not the marker.

        The ``evpn_subnet`` resource is this driver's actual-state marker,
        written when the subnet was created and refreshed only when its cidr
        or next_server change. Compiling from it meant a subnet's other
        fields — its dhcp flag, its resolvers, its routes — reached a guest
        once and never again.
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
        pool = ipam.Ipam({subnet: list(self.list_ports(subnet))})
        reserved = set(self._catalog_reservations(subnet))
        if not subnet.ip_range:
            # Without an explicit ip_range the pool spans the whole CIDR:
            # keep network/broadcast and gateway ("via") addresses out.
            reserved |= {int(subnet.cidr[0]), int(subnet.cidr[-1])}
            for route in subnet.dump_to_simple_view().get("routers") or []:
                if route.get("via"):
                    reserved.add(int(netaddr.IPAddress(route["via"])))
        for ip in sorted(reserved):
            try:
                pool.allocate_ip(subnet, target_ip=netaddr.IPAddress(ip))
            except ipam.IpamNoIPsAvailable:
                break
            except Exception:
                # Already taken by a port — nothing to hold back.
                continue
        return pool.allocate_ip(subnet, target_ip=target_ip)

    @staticmethod
    def _catalog_reservations(subnet: models.Subnet) -> tp.List[int]:
        """Addresses the catalog ledger already owns in this subnet.

        The ledger (``/v1/network/catalog/addresses/``) and this pool are two
        allocators over one subnet; without this the driver happily hands a
        port an address an operator explicitly reserved.
        """
        from exordos_core.user_api.network.dm import models as net_api_models

        reserved = []
        try:
            for addr in net_api_models.Address.objects.get_all(
                filters={"subnet": dm_filters.EQ(subnet.uuid)}
            ):
                if addr.address and addr.address in subnet.cidr:
                    reserved.append(int(netaddr.IPAddress(addr.address)))
        except Exception:
            LOG.exception("Could not read address reservations of %s", subnet.uuid)
        return reserved

    def _build_evpn_port(
        self, port: models.Port, subnet: models.Subnet, publish: bool = True
    ) -> evpn_models.EvpnPort:
        """Compile a port into its target resource.

        ``publish`` also records what it expanded into as generated NFs, and
        lets the objects a port compiles against be seeded on the way: the
        subnet's services, its default group. The staleness check builds the
        same resource only to compare it and must write nothing at all — it
        runs for every port of every subnet on every loop, and a read that
        seeds is a read that can fail, race, and rewrite. Anything not seeded
        yet simply compiles to a resource that differs from the deployed one,
        which is exactly what the check is for: the next update seeds it.
        """
        nfs = self._host_services(port, subnet, seed=publish)
        dhcp = self._dhcp_record(subnet)
        # The host-local DNS responder builds the internal zone from a
        # record's name (<name>.<suffix> -> ipv4 + reverse PTR).
        name = self._guest_name(port)
        if name:
            # The name is the guest's; the zone it joins is the network's
            # `dns` function's, and travels with that function's slice.
            dhcp["name"] = name
        security_rules = self._compile_security_rules(port, subnet, seed=publish)
        fips = self._compile_fips(port, claim=publish)
        if publish:
            self._materialize_generated_nfs(port, security_rules, fips)
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
            # Anti-spoof, enforced by the agent on br-int. The port's own
            # column is the surface; the compiler only has to carry it, and
            # a port that predates the field is protected by default.
            port_security=bool(getattr(port, "port_security", True)),
            agent_uuid=port.node,
        )

    # --- network functions ------------------------------------------------

    def _host_services(
        self, port: models.Port, subnet: models.Subnet, seed: bool = True
    ) -> tp.List[dict]:
        """The functions serving this port, from the objects that own them.

        DHCP belongs to the subnet (every value it hands out is the
        subnet's), the resolver and the netboot/metadata proxy to the
        network. They are seeded with the installation's defaults when the
        subnet and network are created (`_seed_service_nfs`) and are then
        the caller's to edit — which is why they are read here rather than
        invented from `[evpn]` options.

        Two switches, both pre-existing: the subnet's own ``dhcp`` flag, and
        the ``simple`` port kind's ``dhcp``/``dns`` toggles — the friendly
        surface of "this guest manages its own addressing". They now mean
        "do not attach the subnet's (network's) function to this port",
        which is the same thing said about an object.
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
            # The port says *which* functions serve it; what each answers
            # with rides in its own `evpn_nf` slice, so editing a network's
            # resolver does not re-hash a single port.
            services.append({"kind": nf.kind, "nf": str(nf.uuid)})
        return services

    def _service_nfs(self, subnet: models.Subnet, seed: bool = True) -> tp.List[tp.Any]:
        """This subnet's and its network's service functions, seeded if new.

        Ordered dhcp, dns, proxy so the compiled list is stable — the target
        resource's hash must not move because a query came back differently.
        """
        from exordos_core.user_api.network.dm import models as net_api_models

        if seed:
            self._seed_service_nfs(subnet)
        found = {}
        for filters in (
            {"owner_subnet": dm_filters.EQ(subnet.uuid)},
            {"owner_network": dm_filters.EQ(subnet.network)},
        ):
            # Ordered, because "whichever the query returned first" is not an
            # answer: the slice this compiles to is keyed by the function's
            # uuid, so a winner that changes between iterations makes the host
            # install and collect the same function for ever. A unique index
            # now keeps duplicates from existing at all; this keeps a stand
            # that already has some from thrashing until they are cleaned up.
            for nf in sorted(
                net_api_models.NetworkFunction.objects.get_all(filters=filters),
                key=lambda nf: str(nf.uuid),
            ):
                found.setdefault(nf.kind, nf)
        return [found[kind] for kind in ("dhcp", "dns", "proxy") if kind in found]

    def _default_group_rules(self, subnet: models.Subnet) -> tp.Optional[list]:
        """What a subnet's default group permits, as this version seeds it."""
        default_identity = self._default_identity_group(subnet)
        if default_identity is None:
            return None
        return [
            # Out: anything the guest starts.
            {"direction": "egress", "proto": "any"},
            # In: only what its own subnet sends it — by *membership*, not by
            # address. Every port on the subnet joins its default group, so
            # this holds exactly as before, except that a neighbour cannot
            # claim to be one of us by choosing an address. Replies to what
            # the guest started come back on the conntrack path, not through
            # a rule, so this is genuinely "unsolicited from neighbours".
            {
                "direction": "ingress",
                "proto": "any",
                "remote_group": str(default_identity.uuid),
            },
        ]

    def refresh_generated(self, subnet: models.Subnet) -> None:
        """The subnet's services and its default group, kept current.

        Both are seeded on first use and both are derived from the
        installation's own settings, so both go stale in exactly the same
        way: an operator who upgrades gets the new defaults only where a
        port happened to be recompiled. Neither ever overwrites what an
        operator edited — provenance is what separates the two.
        """
        self._seed_service_nfs(subnet)
        self._refresh_default_group(subnet)
        self._refresh_address_sets(subnet)
        self._refresh_published_hosts()

    def _refresh_published_hosts(self) -> None:
        """Tell every host of this installation what it publishes now.

        Installation-wide work in a per-subnet hook, which is not where it
        belongs — but `refresh_generated` is the only periodic pass the
        driver interface gives, and this needs one: a floating address is
        allocated against *one* port on *one* host, while the door it opens
        has to be reachable from the overlays of all of them. Left to the
        port path, a second hypervisor would learn about the address only
        when something unrelated happened to touch a port of its own.

        Cheap when nothing moved: `_ensure_host` compares the rendered hash
        and writes only on a difference.
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
        """Create the service functions this subnet's network is missing.

        Zero-config is the point: a caller who creates a network and a
        subnet gets DHCP, a resolver and netboot without asking for them.
        Seeding them as objects — with the installation's `[evpn]` defaults
        as their initial config — is what makes them visible and editable
        afterwards, instead of a decision buried in the compiler.

        Idempotent, and it never rewrites a function that already exists:
        an operator's edit is not something to reconcile away.
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
                    "forwarders": list(CONF.evpn.dns_forwarders),
                    "zone_suffix": CONF.evpn.dns_zone_suffix,
                    # The name machines know this installation by resolves,
                    # inside a tenant's namespace, to the hypervisor that
                    # serves them — the only address of the platform an
                    # overlay guest can reach.
                    "platform_names": [CONF.boot_api.gc_host],
                },
            ),
            "proxy": (
                "owner_network",
                subnet.network,
                {
                    "forwards": _default_proxy_forwards(subnet),
                    "ports": _platform_ports(),
                    "apis": list(CONF.evpn.proxy_apis),
                },
            ),
        }
        for kind, (owner_field, owner_uuid, config) in wanted.items():
            if owner_uuid is None:
                continue
            existing = net_api_models.NetworkFunction.objects.get_all(
                filters={
                    "kind": dm_filters.EQ(kind),
                    owner_field: dm_filters.EQ(owner_uuid),
                }
            )
            existing = sorted(existing, key=lambda nf: str(nf.uuid))
            if existing:
                # An operator's edit is not something to reconcile away — but
                # a function nobody has touched is ours to keep current, and
                # provenance is exactly that distinction. Without this, a
                # default that was empty when the subnet was created (the
                # proxy's grants, before anything derived them) stays empty
                # for the life of the installation and no upgrade can reach
                # it.
                nf = existing[0]
                # ... except for the kinds that are the platform's own,
                # which are refreshed whatever provenance they carry. They
                # are not a caller's to have taken over (the API refuses
                # it), and one that did — an older stand, a manifest writing
                # the row directly — is pointing this network's hypervisors
                # at upstreams the installation never granted. That is
                # brought back, not preserved.
                platform = kind in net_api_models.NetworkFunction.PLATFORM_KINDS
                if platform or (
                    nf.provenance == net_api_models.NFProvenance.GENERATED.value
                ):
                    # `sync_generated`, not `update`: an ordinary update is
                    # how an *operator* edits a function, and it marks the
                    # function theirs from then on. The installation
                    # refreshing its own default that way would hand it to
                    # nobody and never be able to refresh it again.
                    stale_provenance = (
                        nf.provenance != net_api_models.NFProvenance.GENERATED.value
                    )
                    if dict(nf.config or {}) != config or stale_provenance:
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

    def _materialize_generated_nfs(
        self, port: models.Port, rules: tp.List[dict], fips: tp.List[dict]
    ) -> None:
        """Publish the port's compiled functions as generated NF records.

        The friendly ``simple`` kind is the source of truth, but the NFs it
        expands into are real objects in /v1/network/nfs/ (read-only,
        ``provenance=generated``) so the substrate stays inspectable rather
        than implied. Best-effort: a bookkeeping failure must never stop
        the port from being wired.
        """
        wanted = {}
        if rules:
            wanted["splitter"] = {"rules": rules}
        if fips:
            wanted["fip"] = {"address": fips[0]["public"]}
        try:
            self._sync_generated_nfs(port, wanted)
        except Exception:
            LOG.exception("Could not materialize generated NFs of port %s", port.uuid)

    @staticmethod
    def _sync_generated_nfs(port: models.Port, wanted: tp.Dict[str, dict]) -> None:
        from exordos_core.user_api.network.dm import models as net_api_models

        existing = {
            nf.kind: nf
            for nf in net_api_models.NetworkFunction.objects.get_all(
                filters={"owner_port": dm_filters.EQ(port.uuid)}
            )
        }
        for kind, config in wanted.items():
            nf = existing.pop(kind, None)
            if nf is None:
                net_api_models.NetworkFunction(
                    uuid=sys_uuid.uuid4(),
                    name="%s-%s" % (kind, port.uuid),
                    project_id=port.project_id,
                    kind=kind,
                    config=config,
                    provenance=net_api_models.NFProvenance.GENERATED.value,
                    owner_port=port.uuid,
                ).insert()
            else:
                nf.sync_generated(config)
        for nf in existing.values():
            nf.collect_generated()

    def _compile_fips(self, port: models.Port, claim: bool = True) -> list:
        """Compile the port's floating IP (`fip`).

        The ``simple`` kind's ``public`` slot: ``{address}`` reuses an
        allocated object, ``{floating_from}`` auto-allocates one. It
        resolves to a 1:1 NAT mapping ``{public: <ip>}``.

        ``claim`` says whether the ledger may be written — the compile runs
        twice, once to publish and once to compare, and only the first is
        entitled to record that this port is now using the address.
        """
        addr = self._public_address(port)
        if addr is None or addr.address is None:
            if claim:
                self._settle_associations(port.uuid, None)
            return []
        if claim:
            self._settle_associations(port.uuid, addr)
        return [{"public": str(addr.address)}]

    def _public_address(self, port: models.Port):
        """The address object the port's ``public`` slot resolves to."""
        config = getattr(port, "config", None)
        if config is None or getattr(config, "KIND", None) != "simple":
            return None
        public = getattr(config, "public", None)
        if not public or not isinstance(public, dict):
            return None
        from exordos_core.user_api.network.dm import models as net_api_models

        addr_uuid = public.get("address")
        if addr_uuid:
            # Reuse a caller's already-allocated public address-object.
            return net_api_models.Address.objects.get_one_or_none(
                filters={"uuid": dm_filters.EQ(addr_uuid)}
            )
        if public.get("floating_from"):
            # Auto-allocate (once) a floating address out of the named
            # subnet, owned by this port — idempotent via owner_port, which
            # is the ownership fact itself. (Not also by origin: an address
            # whose origin was rewritten on the way in would go unfound and
            # this branch would allocate a fresh public address on every
            # single recompile.)
            #
            # A subnet and not a network: which addresses these are is the
            # whole question here, and a network answers it only when it
            # happens to have one subnet. Naming a network with two — the
            # flat one has its management subnet and its boot subnet, and a
            # pool of floating addresses is naturally a third — took
            # whichever came back first.
            addr = net_api_models.Address.objects.get_one_or_none(
                filters={"owner_port": dm_filters.EQ(port.uuid)}
            )
            if addr is not None:
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
        """Make the ledger say what this port is actually using.

        The ledger is what says an address is spoken for: it is why a second
        port citing it is refused, why it cannot be freed or deleted under
        the guest answering on it, and why a caller reading the catalog can
        see where a public address went. None of that follows from the port
        pointing at the address, because a pointer is only visible from the
        side that holds it.

        Which is also why the *release* half belongs here and not only on
        the port's delete path. Unbinding an address (`--no-public`, or
        naming a different one) leaves the port in place, so nothing else
        would ever clear the old pointer — and an address marked as used by
        a port that stopped using it can be neither re-cited nor released,
        which is exactly the stuck state this bookkeeping exists to
        prevent.

        Runs on the publishing pass only, and writes only what changed, so
        the comparison pass stays a read and an unchanged port stays quiet.
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
        ``{direction, proto, port?, remote?}``. ``remote`` keeps its meaning
        from the direction — a destination for egress, a source for ingress —
        and the agent matches the right field; collapsing both onto ``dst``
        silently turned every ingress rule into an egress one.

        Both spellings are accepted on input: a generated ``splitter`` NF
        publishes what this method produced, so a graph pointing back at one
        would otherwise re-read ``proto``/``remote`` as absent and widen the
        rule to "any protocol, any peer".
        """
        compiled = {
            "direction": rule.get("direction", "egress"),
            "proto": rule.get("protocol") or rule.get("proto") or "any",
        }
        if rule.get("port") is not None:
            compiled["port"] = int(rule["port"])
        already = rule.get("remote_identity")
        if already is not None:
            compiled["remote_identity"] = int(already)
        if rule.get("remote_set") is not None:
            compiled["remote_set"] = int(rule["remote_set"])
        group = rule.get("remote_group")
        if group is not None:
            # The agent matches a number, not a name: what travels on the
            # wire is the group's identity — or, for a group past its
            # network's bit budget, the number that joins the two halves of
            # its address set. Which of the two a group has was settled when
            # it was created, so a rule compiles the same way for as long as
            # the group exists. An unresolvable reference compiles to
            # nothing rather than to a rule that would match whatever number
            # happens to pass — the caller keeps the deny-all default until
            # the reference is fixed.
            carried = OvsEvpnNetworkDriver._identity_group(group)
            if carried is None:
                LOG.error("Rule names identity group %s, which is gone", group)
                return {"direction": compiled["direction"], "proto": "none"}
            # Named by uuid on the way in, carried as a number on the way
            # out: the agent matches what the fabric can hold, and the
            # generated function this is published as stays re-readable.
            if carried.identity is not None:
                compiled["remote_identity"] = int(carried.identity)
            else:
                compiled["remote_set"] = int(carried.conj_id)
        remote = rule.get("remote_ip") or rule.get("remote")
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
        """What this port stamps on everything its guest sends.

        Its subnet's default group unless the port opted out, plus every
        group it names — one bit each, so a rule matching one bit admits the
        guest whatever else it belongs to. Zero means unidentified: nothing
        is stamped, and no group rule anywhere can match it, which is what a
        port that left the subnet's group and named none of its own gets.

        A group past its network's bit budget contributes nothing here and
        is not an error: it is carried the other way round, as a set of its
        members' addresses shipped to the hosts, so what makes this port a
        member is its address being in that set — not anything it stamps.
        """
        config = getattr(port, "config", None)
        mark = 0
        # In its subnet's group unless it says otherwise: joining a group of
        # its own adds access rather than trading the subnet's away, and a
        # workload that wants neither says so explicitly.
        if subnet is not None and getattr(config, "subnet_group", None) is not False:
            default = self._default_identity_group(subnet, seed=seed)
            if default is not None and default.identity is not None:
                mark |= int(default.identity)
        for named in getattr(config, "identity_groups", None) or []:
            group = self._identity_group(named)
            if group is not None and subnet is not None:
                # The subnet may carry its network as a prefetched object
                # rather than a uuid, depending on how the row was loaded.
                network = getattr(subnet.network, "uuid", subnet.network)
                if str(group.network) != str(network):
                    # A bit means whatever *this* network says it means, so a
                    # group from another one is a membership claim in a group
                    # the port was never admitted to. The API refuses it; a
                    # row that got in another way is refused here, because
                    # this is where it would take effect.
                    LOG.error(
                        "Port %s names identity group %s of another network, "
                        "not stamping it",
                        port.uuid,
                        named,
                    )
                    continue
            if group is None:
                # The API refuses to delete a group a port is in, so this is
                # a row removed out of band. Fail closed — the port keeps
                # the membership it can still prove — and say so, because a
                # guest quietly losing the group that admitted it somewhere
                # is not something to discover from a packet capture.
                LOG.error(
                    "Port %s is in identity group %s, which is gone",
                    port.uuid,
                    named,
                )
                continue
            if group.identity is None:
                # An address-set group. Nothing to stamp; the port is in it
                # by being in the set that ships to the hosts.
                continue
            mark |= int(group.identity)
        return mark

    def _default_identity_group(self, subnet: models.Subnet, seed: bool = True):
        """The subnet's own group, seeded on first use.

        A default rule has to name something, and naming the subnet's CIDR
        would leave the one rule that applies to every guest deciding who is
        "us" by address — the very thing an identity exists to stop. So the
        subnet gets a group, every port on it joins by default, and the
        default rule names that group.
        """
        from exordos_core.user_api.network.dm import models as net_api_models

        # Scoped by the network, not by the name alone. A name is not an
        # identity: any project may create a group called anything, so a
        # lookup on the name by itself would find — and make this subnet's
        # default — a row belonging to somebody else, whose deletion then
        # takes the subnet's default rules with it.
        existing = sorted(
            net_api_models.IdentityGroup.objects.get_all(
                filters={
                    "name": dm_filters.EQ(self._default_group_name(subnet)),
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
                name=self._default_group_name(subnet),
                description="Default identity of subnet %s" % subnet.uuid,
                project_id=subnet.project_id,
                network=subnet.network,
                # A bit while the network has one, an address set once they
                # are spent — the same allocation the API path takes. Asking
                # for a bit outright is what used to make a subnet on a full
                # network compile to deny-all: the refusal landed in the
                # `except` below and the subnet was left without a default at
                # all, which is the state the create-time gate existed to
                # prevent and this is what replaced it.
                **net_api_models.IdentityGroup.allocate(subnet.network),
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
    def _default_group_name(subnet: models.Subnet) -> str:
        return "subnet-%s" % subnet.uuid

    @staticmethod
    def _guest_name(port: models.Port) -> tp.Optional[str]:
        """How this guest is known inside its network.

        The zone answers for guests, not for ports, and the platform names
        neither: it creates a node's port with no name at all. So every
        guest of every overlay was absent from a zone the responder builds,
        the suffix is configured for and nothing ever put a record in —
        `dig sdn-a.internal` answered NXDOMAIN for a machine two hops away.

        A port's own name wins when it has one (that is someone saying what
        this interface is called); otherwise the node's, which is the name
        the guest was created under and the one a person would type.
        """
        if port.name:
            return port.name
        if port.node is None:
            return None
        node = models.Node.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(port.node)}
        )
        return node.name if node is not None else None

    def _compile_security_rules(
        self,
        port: models.Port,
        subnet: tp.Optional[models.Subnet] = None,
        seed: bool = True,
    ) -> list:
        """Compile the port's filtering into the agent's allow-list.

        The ``simple`` kind's catalog security groups (ENI-style
        references), lowered to the conntrack allow-list the on-host driver
        installs. A group is an allow-list **in both directions** (AWS-SG
        semantics): attaching one denies everything it does not permit,
        either way.

        A port that names no group of its own is not unfiltered: it gets its
        subnet's default group, seeded with the same shape AWS gives a
        default security group — everything out, and in only from the
        subnet the guest is on. Attaching a group replaces that default,
        which is what makes "attach one and only what it permits gets
        through" true from the first port onward.
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
                # A referenced group that no longer exists must not silently
                # widen the port to allow-all: keep the port filtered and let
                # the deny-all default stand until the reference is fixed.
                # (The API refuses to delete a referenced group; this covers
                # rows removed out of band.)
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
        return rules

    # What a port compiles to when the installation cannot say what should
    # reach it. `proto: none` matches nothing, so the agent installs the
    # allow-list pipeline with no allow in it: the port is isolated rather
    # than open. The same sentinel a dangling security-group reference uses.
    DENY_ALL = ({"direction": "egress", "proto": "none"},)

    def _default_security_rules(
        self, subnet: tp.Optional[models.Subnet], seed: bool = True
    ) -> tp.List[dict]:
        """The subnet's default group, seeded on first use.

        A guest arrives filtered rather than open: it may start anything
        outward, and may be reached from its own subnet — the closest
        honest reading of "from the group itself" in a model where the
        group is the subnet. Anything else in needs a rule, which is what a
        default-deny posture means.

        It is an object like every other function (`splitter`, owned by the
        subnet), so an installation that wants a different default edits it.

        When the default cannot be produced at all — the network is out of
        identity bits, the seed raced and lost, the row was removed out of
        band — the answer is deny, not silence. Returning an empty list here
        meant "no filtering", so a subnet that merely failed to get a group
        handed every one of its guests an unfiltered port and said so only in
        a log line. The failure direction of a security default is not a
        detail: it is the whole of what the default is for.
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
        ]

    def _default_group_nf(self, subnet: models.Subnet, seed: bool = True):
        from exordos_core.user_api.network.dm import models as net_api_models

        existing = sorted(
            net_api_models.NetworkFunction.objects.get_all(
                filters={
                    "kind": dm_filters.EQ("splitter"),
                    "owner_subnet": dm_filters.EQ(subnet.uuid),
                }
            ),
            key=lambda nf: str(nf.uuid),
        )
        if existing:
            return existing[0]
        if not seed:
            return None
        rules = self._default_group_rules(subnet)
        if rules is None:
            # Without a group to name there is no honest default rule: leave
            # the subnet without one rather than fall back to deciding who is
            # "us" by address.
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
        """Agent that wires the port into the dataplane.

        Guests plug into their hypervisor's br-int, so the wiring is owned
        by the hypervisor node's agent (``MachinePool.hypervisor_node``).
        Until the guest's machine is placed on a pool that agent is
        unknown — return None so the caller defers to a later loop.

        A machine on a pool that names no hypervisor has nobody to do it.
        It used to be handed to the guest's own agent, on the reading that
        a node can wire itself — which is true of a machine that *is* a
        host and false of a guest of one: its bridge is on the hypervisor,
        and an agent inside the guest has nothing to put a patch on. The
        resource was created, scheduled to an agent that could never act on
        it, and the port stayed new for ever with nothing anywhere saying
        why. Nothing is written now, and the reason is: the field is set by
        `exordos hypervisors register-agent --pool`, which is also the only
        thing that sets it, so a pool that has not been through it is the
        common way to arrive here.
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
            "has nobody to wire it; run `exordos hypervisors register-agent "
            "--pool %s` on the host that serves this pool",
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
            # The machine is not placed on a pool yet, so the wiring
            # hypervisor is unknown; NetworkService retries next loop.
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
        port.status = nc.PortStatus.ACTIVE.value
        return port

    def update_port(self, port: models.Port) -> models.Port:
        subnet = self._find_subnet(port.subnet)
        res = self._get_resource(evpn_models.EvpnPort.get_resource_kind(), port.uuid)
        new_res = self._build_evpn_port(port, subnet).to_ua_resource(master=subnet.uuid)
        res.update_value(new_res)
        res.agent = self._wiring_agent(port.node) or res.agent
        res.update()
        if res.agent is not None:
            # A port's functions are also the host's: the responder this
            # network needs may have just been switched on or off.
            self._ensure_host(res.agent)
            self._ensure_host_nfs(res.agent, subnet)
        return port

    def port_is_stale(self, port: models.Port, actual_port: models.Port) -> bool:
        """True when what the port compiles to differs from what is deployed.

        A port carries almost nothing itself: its filtering, its network
        functions and its floating addresses all live in objects it merely
        references, and editing one of those leaves the port's own fields
        untouched. Comparing the compiled resource is the only honest test —
        without it the compiler would run once, at creation, and every later
        change would sit in the database with no path to a hypervisor.
        """
        try:
            res = self._get_resource(
                evpn_models.EvpnPort.get_resource_kind(), port.uuid
            )
        except ra_storage_exc.RecordNotFound:
            return False
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
        self._collect_generated_nfs(port.uuid)
        self._release_owned_addresses(port.uuid)
        if node_uuid is not None:
            try:
                self._collect_host_nfs(node_uuid, self._find_subnet(port.subnet))
            except Exception:
                LOG.exception("Could not collect the function slices of %s", node_uuid)
            self._collect_host(node_uuid)

    @staticmethod
    def _collect_generated_nfs(port_uuid: sys_uuid.UUID) -> None:
        """Drop the NFs a departing port generated (best-effort)."""
        from exordos_core.user_api.network.dm import models as net_api_models

        try:
            for nf in net_api_models.NetworkFunction.objects.get_all(
                filters={"owner_port": dm_filters.EQ(port_uuid)}
            ):
                nf.collect_generated()
        except Exception:
            LOG.exception("Could not collect generated NFs of port %s", port_uuid)

    @staticmethod
    def _release_owned_addresses(port_uuid: sys_uuid.UUID) -> None:
        """Give back what a departing port held (best-effort).

        Two different things, and only one of them is the port's to destroy.
        A ``floating_from`` port auto-allocates a public Address owned by it
        (``owner_port``): that one dies with the port, and Address.delete
        refuses to release an owned address ("delete the port instead"), so
        this is that path — without it every create+delete of such a port
        leaks one public address irrecoverably.

        An address the port merely *cited* is the caller's, reserved before
        the port existed and outliving it: keeping a public IP across the
        machine that answered on it is the whole point of the Elastic-IP
        shape. So it is disassociated, not deleted — and it has to be, or
        the address stays marked as in use by a port that is gone, which no
        other port may then cite and nobody may release.
        """
        from exordos_core.user_api.network.dm import models as net_api_models

        try:
            for addr in net_api_models.Address.objects.get_all(
                filters={"association": dm_filters.EQ(port_uuid)}
            ):
                if str(addr.owner_port or "") == str(port_uuid):
                    continue
                addr.association = None
                addr.update()
        except Exception:
            LOG.exception("Could not disassociate the addresses of port %s", port_uuid)
        try:
            for addr in net_api_models.Address.objects.get_all(
                filters={"owner_port": dm_filters.EQ(port_uuid)}
            ):
                # Clear the ownership/use pointers so the model's referential
                # guards pass, then release the row.
                addr.owner_port = None
                addr.association = None
                addr.update()
                addr.delete()
        except Exception:
            LOG.exception("Could not release owned addresses of port %s", port_uuid)

    # --- per-node host resources -----------------------------------------

    def _ensure_host(self, node_uuid: sys_uuid.UUID) -> None:
        """Keep the node's evpn_host resource current.

        Maintained (not just created): the fabric parameters and the host's
        network-function set change over the node's life — a new network
        landing a port there, an edited resolver list — and a create-once
        resource would pin the host to whatever the first port implied.
        """
        published = _published_addresses()
        host = evpn_models.EvpnHost(
            uuid=node_uuid,
            as_number=CONF.evpn.as_number,
            rr_addresses=list(CONF.evpn.rr_addresses),
            published_addresses=published or [],
            agent_uuid=node_uuid,
        )
        new_res = host.to_ua_resource()
        new_res.agent = host.schedule_to_ua_agent()
        try:
            res = self._get_resource(host.get_resource_kind(), node_uuid)
        except ra_storage_exc.RecordNotFound:
            # A host that has none yet has nothing to lose by starting
            # empty; the next pass that can read the ledger fills it in.
            new_res.insert()
            LOG.info("Created evpn_host resource for node %s", node_uuid)
            return
        if published is None:
            # An unreadable ledger is not an empty one. Rewriting the host
            # from it would shut every published door in the installation
            # until a later pass reopened them — leave the host with what it
            # already has, and let that pass do the whole update.
            return
        if res.hash != new_res.hash:
            res.update_value(new_res)
            res.update()
            LOG.info("Updated evpn_host resource for node %s", node_uuid)

    # --- address sets ----------------------------------------------------

    def _serving_hosts(self) -> tp.Set[sys_uuid.UUID]:
        """The hosts that have a guest on this network right now."""
        subnets = {str(net.uuid) for net in self.list_subnets()}
        return {
            res.agent
            for res in ua_models.TargetResource.objects.get_all(
                filters={
                    "kind": dm_filters.EQ(evpn_models.EvpnPort.get_resource_kind()),
                }
            )
            if res.agent is not None and str(res.master) in subnets
        }

    def _set_members(self, group) -> tp.List[str]:
        """The addresses of the ports in an address-set group.

        Membership is read the same way the mark is stamped — a port's own
        list, plus its subnet's default unless it opted out — so the two
        halves of the hybrid cannot drift into meaning different things.

        A port with no address yet contributes nothing rather than a hole:
        it is a member that cannot be recognised, which is the fail-closed
        reading and repairs itself on the pass after it is addressed.
        """
        from exordos_core.compute.dm import models as compute_models

        members = set()
        for subnet in self.list_subnets():
            default = self._default_identity_group(subnet, seed=False)
            is_default = default is not None and str(default.uuid) == str(group.uuid)
            for port in compute_models.Port.objects.get_all(
                filters={"subnet": dm_filters.EQ(subnet.uuid)}
            ):
                config = getattr(port, "config", None)
                named = [
                    str(named)
                    for named in getattr(config, "identity_groups", None) or []
                ]
                joined = str(group.uuid) in named or (
                    is_default and getattr(config, "subnet_group", None) is not False
                )
                if joined and port.ipv4:
                    members.add(str(port.ipv4))
        return sorted(members)

    def _refresh_address_sets(self, subnet: models.Subnet) -> None:
        """Keep this network's address sets on the hosts that serve it.

        The other half of the hybrid. A group past its network's sixteen
        bits carries no mark, so nothing in the packet says who sent it;
        what says so instead is this — the members' addresses, shipped to
        every host with a guest on the network, where they become one half
        of a conjunctive match whose other half is the rules naming the
        group.

        Its own resource per (group, host), like a function's settings, for
        the same reason: a member joining or leaving rewrites one small
        resource per host and re-hashes no port, so no guest's data path is
        touched by a fact about another guest. That is the failure mode the
        whole address-set family is otherwise known for.

        Emitted from the periodic hook rather than from the port path,
        because membership changes on the *member's* port while the flows
        that need it belong to every host on the network.
        """
        from exordos_core.user_api.network.dm import models as net_api_models

        network = getattr(subnet.network, "uuid", subnet.network)
        groups = [
            group
            for group in net_api_models.IdentityGroup.objects.get_all(
                filters={"network": dm_filters.EQ(network)}
            )
            if group.identity is None
        ]
        hosts = self._serving_hosts()
        wanted = set()
        for group in groups:
            members = self._set_members(group)
            for node_uuid in hosts:
                wanted.add(self._ensure_address_set(group, members, node_uuid, subnet))
        self._collect_address_sets(subnet, wanted)

    def _ensure_address_set(
        self,
        group,
        members: tp.List[str],
        node_uuid: sys_uuid.UUID,
        subnet: models.Subnet,
    ) -> sys_uuid.UUID:
        slice_uuid = sys_uuid.uuid5(SET_SLICE_NS, "%s:%s" % (group.uuid, node_uuid))
        compiled = evpn_models.EvpnAddressSet(
            uuid=slice_uuid,
            group=group.uuid,
            conj_id=int(group.conj_id),
            addresses=members,
            agent_uuid=node_uuid,
        )
        new_res = compiled.to_ua_resource(master=subnet.uuid)
        new_res.agent = compiled.schedule_to_ua_agent()
        try:
            res = self._get_resource(compiled.get_resource_kind(), slice_uuid)
        except ra_storage_exc.RecordNotFound:
            new_res.insert()
            LOG.info(
                "Created the address set of group %s on node %s", group.uuid, node_uuid
            )
            return slice_uuid
        if res.hash != new_res.hash:
            res.update_value(new_res)
            res.update()
            LOG.info(
                "Updated the address set of group %s on node %s", group.uuid, node_uuid
            )
        return slice_uuid

    def _collect_address_sets(
        self, subnet: models.Subnet, wanted: tp.Set[sys_uuid.UUID]
    ) -> None:
        """Drop the sets of this network that nothing wants any more.

        A host that lost its last guest on the network, or a group that
        stopped being one, leaves member flows behind — and a stale set is
        not inert: it is an allow-list somebody's rule may still join.
        """
        for res in self._list_resources(
            evpn_models.EvpnAddressSet.get_resource_kind(), subnet.uuid
        ):
            if res.uuid in wanted:
                continue
            res.delete()
            LOG.info("Collected the address set slice %s", res.uuid)

    def _ensure_host_nfs(
        self,
        node_uuid: sys_uuid.UUID,
        subnet: models.Subnet,
        nfs: tp.Optional[tp.List[tp.Any]] = None,
    ) -> None:
        """Deliver this subnet's functions to the host serving its guests.

        One slice per (host, function), keyed by uuid5 so the same function
        on two hosts converges independently and a host that already has it
        is left alone. What the slice carries is the function's own
        configuration — which is why an operator editing a network's
        resolver moves these resources and nothing else: no port's hash
        changes, so no guest's data path is touched.
        """
        # The subnet's functions are the subnet's, not each host's: read once
        # by the caller when it is about to visit several hosts. Re-reading
        # (and re-seeding) them per agent multiplied the whole seeding path by
        # the number of hypervisors serving the subnet, on every reconcile.
        for nf in self._service_nfs(subnet) if nfs is None else nfs:
            slice_uuid = sys_uuid.uuid5(NF_SLICE_NS, "%s:%s" % (nf.uuid, node_uuid))
            compiled = evpn_models.EvpnNf(
                uuid=slice_uuid,
                kind=nf.kind,
                vni=self._vni,
                config=dict(nf.config or {}),
                agent_uuid=node_uuid,
            )
            new_res = compiled.to_ua_resource(master=subnet.uuid)
            new_res.agent = compiled.schedule_to_ua_agent()
            try:
                res = self._get_resource(compiled.get_resource_kind(), slice_uuid)
            except ra_storage_exc.RecordNotFound:
                new_res.insert()
                LOG.info(
                    "Created the %s slice of %s on node %s", nf.kind, nf.uuid, node_uuid
                )
                continue
            if res.hash != new_res.hash:
                res.update_value(new_res)
                res.update()
                LOG.info(
                    "Updated the %s slice of %s on node %s", nf.kind, nf.uuid, node_uuid
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
        """Drop this subnet's function slices from a host that lost its guests.

        A slice is what a host answers a guest with, so it has no reason to
        outlive the guests — and it does not merely idle: its arrival is
        what makes the host serve that VNI at all. Left behind, the host
        keeps a network it has nothing on.

        Scoped by ownership, because the two are not the same question:
        `dhcp` belongs to the subnet and goes with the host's last port on
        it, while `dns` and `proxy` belong to the network and stay while
        the host still serves any of its subnets.
        """
        from exordos_core.user_api.network.dm import models as net_api_models

        subnets = {str(net.uuid) for net in self.list_subnets()}
        serving = {
            str(res.master)
            for res in ua_models.TargetResource.objects.get_all(
                filters={
                    "kind": dm_filters.EQ(evpn_models.EvpnPort.get_resource_kind()),
                    "agent": dm_filters.EQ(node_uuid),
                }
            )
        } & subnets
        # The functions as they are, never seeded: a delete path must not
        # create what it is here to collect.
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

    # --- route reflector ---------------------------------------------------

    def _ensure_rr(self) -> None:
        """Maintain the bgp_rr resource for the configured RR agent."""
        if not CONF.evpn.rr_agent:
            return
        rr_uuid = sys_uuid.UUID(CONF.evpn.rr_agent)
        rr = evpn_models.BgpRr(
            uuid=rr_uuid,
            as_number=CONF.evpn.as_number,
            peer_prefixes=list(CONF.evpn.rr_peer_prefixes),
            agent_uuid=rr_uuid,
        )
        new_res = rr.to_ua_resource()
        new_res.agent = rr.schedule_to_ua_agent()
        try:
            res = self._get_resource(rr.get_resource_kind(), rr_uuid)
            if res.hash != new_res.hash:
                res.update_value(new_res)
                res.update()
        except ra_storage_exc.RecordNotFound:
            new_res.insert()
            LOG.info("Created bgp_rr resource for agent %s", rr_uuid)
