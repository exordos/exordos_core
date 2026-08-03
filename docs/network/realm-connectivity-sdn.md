# Realm Connectivity SDN (EVPN/VXLAN)

Design document for realm network isolation and public connectivity in the
exordos ecosystem. Status: **approved design, increment 1 in progress**:
the data-plane spike passed 12/12 (incl. fail-static and DVR egress),
the `ovs_evpn` CP driver with VNI/RT/MTU allocation and
`evpn_port`/`evpn_host` target computation is implemented, and the
host-side evpn capability driver (gcl_sdk) renders them into
evpn_connector/gobgpd configs, incl. the bgp_rr reflector kind; idle
evpn_host resources are garbage-collected. Validated end to end: a cross-repo
functional test drives the real CP loop into real target resources and
feeds them to the real host driver, and the live spike matrix passes
12/12 (`sdn` branches). Step 0 (border NAT capability) is implemented and validated
end-to-end. The control-plane API model (below) was designed 2026-07-17.

## Context and goals

An ecosystem installation hosts **realms** — nested, fully fledged
`exordos_core` installations running as VMs on the ecosystem's hardware. The
current network model is a single flat L2 segment (`flat_bridge` driver, ISC
DHCP on a Linux bridge) shared by everything, which gives:

- no isolation between realms (shared broadcast domain, colliding private
  CIDRs on shared hardware);
- no managed public ingress/egress for realm services.

Public connectivity was partially addressed by **Step 0**: a `border_agent`
universal-agent capability on the core node providing egress SNAT and
`(ip, port)` DNAT forwards via nftables. This document defines the target
architecture that adds isolation and generalizes connectivity, and the scope
of the first SDN increment.

## Architecture decisions

The decisions below were fixed during a full design review (2026-07-17).

### 1. Data plane: OVS + gobgp, reusing `evpn_connector`

The data plane reuses the `evpn_connector` project (gobgp over gRPC + Open
vSwitch/VXLAN) **for EVPN forwarding only**. It already implements EVPN
Type-5 routes, VRFs, ECMP, ARP proxying and anycast prefixes with health
checks, and consumes simple per-host JSON client configs — a natural fit
for universal-agent delivery. It is treated as a **third-party
dependency**, kept unmodified: everything above EVPN (the NF graph,
DHCP/DNS/proxy) is owned by the agent on a separate bridge (see
"Dataplane ownership: two bridges"). FRR + native Linux VXLAN (rewrite of
the reconcile logic) and OVN (heavy dependency, foreign object model)
were considered and rejected.

### 2. Overlay model: L3 EVPN Type-5 (one realm = one VRF)

Each realm is assigned a VRF (VNI + route target) by the parent installation.
Every VM is announced as a `/32` Type-5 route into its VRF; there is no
stretched L2 and no BUM traffic; ARP is answered locally by the host
(ARP proxy in `evpn_connector`). Realms are isolated even with overlapping
private CIDRs. L2 (Type-2) segments remain a possible future option, not part
of the base design.

### 3. Boot network: bare metal only; VMs netboot inside the overlay

The PXE boot network (`next_server` + `ip_discovery_range` subnet) remains
an **infrastructure** network for provisioning **bare metal** (the core
node, hypervisors): iPXE firmware must arrive before any host-side
smartness exists, so physical nodes keep the flat underlay segment and
its L2 adjacency. Realms never touch it.

VMs do not use it at all. A QEMU VM's NIC ROM is already iPXE (verified
on the dev stand: the libvirt driver only emits `<rom>` to override the
file, so the distro `ipxe-qemu` ROM stays enabled; `iface_rom_file` in
`LibvirtPoolDriverSpec` can pin a custom build), so the TFTP stage is
unnecessary: the `dhcp` NF hands out a `filename` HTTP URL
pointing at the host-local `proxy` NF (see the NF catalog), which serves
the netboot script, kernel/initrd and images from the repo chain through
the agent's cache. All provisioning traffic terminates on the VM's own
hypervisor: no flat NIC on VMs (the shared-boot-L2 cross-realm channel is
eliminated by construction), no VXLAN/MTU concerns for firmware, and the
recipe recurses — each level's hypervisors netboot their own VMs. Netboot
serving follows the node lifecycle: enabled while the node is being
provisioned, disabled afterwards, re-enabled for reinstall.

Direct image write by the hypervisor agent (no netboot for VMs at all)
remains a possible later optimization; it is no longer needed to remove
the L2-underlay dependency for VMs.

### 4. Host-local DHCP for overlay networks

Guest networks in the overlay cannot reach the central ISC DHCP by broadcast.
Each hypervisor runs a small **host-local DHCP responder** answering its
local VMs from control-plane facts (`Port.mac/ipv4`, `Subnet.routers/
dns_servers`, MTU). No DHCP relay, no runtime dependency on the core node for
address assignment. The responder config is delivered by the same per-host
agent resource as the EVPN configs.

**One process, one namespace per VNI.** The responder — DHCP,
the internal resolver and the netboot/metadata proxy in one daemon — used to
serve every tenant on the host from a single trunk port in the root
namespace, scoped only by a VLAN it had to recover after OVS stripped it,
with a policy-routing table per VNI so overlapping guest CIDRs would not
collide. Each VNI now has a namespace on the host (`vni<n>` — the one the
DVR egress already used, created for the VNI itself rather than for its
gateway) holding one internal port of br-int (`nf<n>`) that carries the
well-known metadata address. The daemon stays single — density on a
hypervisor with many tenants is the reason — but runs a thread per VNI that
enters that namespace once with `setns`, so its raw socket and the proxy's
listener belong to the tenant. What that removed: the per-VNI routing
tables and `ip rule`s, the rp_filter tweaks, the VLAN in the guest records,
the `PACKET_AUXDATA` recovery and the `SO_BINDTODEVICE` listeners.

Delivery is explicit in both directions and per guest: the intercept
rewrites the frame's destination to the VNI's port (a frame addressed to
the gateway reaches a socket in a namespace only if the interface is
promiscuous — better to say what is happening than to listen to
everything), and the answer returns on that guest's own flow, matched on
its MAC. DHCP replies are addressed to the guest that asked rather than
flooded to the segment, and the namespace is taught each guest's neighbour
instead of ARPing for it. Because a guest's whole function path is one
cookie, wiring a newcomer cannot disturb a working neighbour.

### 5. Public connectivity topology is an installation parameter

The design must work at the **minimum topology**: a single uplink, no eBGP to
the provider (static next-hop), as on the current development stand. Richer
topologies (multiple uplinks, provider eBGP, anycast) are expressed by
placing the **edge role** on more nodes. "Border" is a role/capability on
nodes, never a first-class appliance; the Step 0 border on the core node is
the degenerate case "edge role on exactly one node".

### 6. Egress: distributed SNAT (DVR) by default

Each hypervisor SNATs traffic of its local realm VMs into its own uplink IP
(per-VRF route leak to the global table): no SPOF, no hairpin, horizontal
scaling. Trade-off: the realm's outbound traffic originates from many node
IPs.

Two mandatory constraints on the SNAT rule:

- traffic sourced from addresses of public (routed) networks — e.g. a
  `fip` address held by a local VM — is routed, never SNATed,
  otherwise routed ingress would break on the return path;
- realm VMs reach the parent's infrastructure endpoints (netboot, core
  API, repo chain) through the host-local `proxy` NF, not through the
  leak: the leak carries plain internet egress only, and it MUST NOT
  expose the BGP/RR ports (see decision 8).

**The egress guard.** "Plain internet egress only" was a
statement about intent that nothing enforced: two ports were blocked by
number and the underlay carrying the whole fabric — the hypervisors' VTEPs,
the reflector, the control plane's APIs, ovsdb, ssh — was one hop from every
guest. `_ensure_fabric_guard` is what makes the sentence true. In the egress
namespace's FORWARD chain, per VNI, reconciled on every iteration:

- **everything that is not globally routable**, whole: RFC1918, CGNAT
  (100.64/10), link-local, loopback, multicast, reserved. This is the rule,
  and it is deliberately blunt: "internal" is a *destination*, not a
  service, and a guard built from ports is wrong the day somebody moves
  one. An internal network **behind a router** — a second site, a
  management VLAN, a reflector in another subnet — is in no routing table
  on this host, so anything derived from what the host can see would have
  covered it by port number and by nothing else;
- every network **this host is directly attached to** that is *not* already
  inside that space — which is the publicly-addressed installation, and
  only it. Read from the host rather than configured: a list an operator
  maintains goes stale silently;
- the per-VNI **transfer range** as one prefix. It has to be there and it is
  the piece that is easy to miss — a transfer address belongs to the *root*
  namespace, so a guest allowed to reach one reaches everything bound to
  `0.0.0.0` on the hypervisor, one hop short of everything else the guard
  covers. Guarded whole rather than per /30 so the set does not churn with
  the VNIs a host happens to serve;
- the fabric's own protocols wherever they are addressed — BGP (179),
  gobgp's gRPC (50051) and **VXLAN (4789)** — as a backstop for the
  publicly-addressed fabric, where the space above covers nothing. VXLAN is
  the one that mattered most: the tunnel is accepted from any source
  (`remote_ip=flow`) and classified by `tun_id` alone, so a guest that could
  put a datagram on port 4789 of any hypervisor had it decapsulated into
  whatever VNI it named, carrying whatever identity it chose in the GBP
  bits — the overlay's isolation and the identity groups both bypassed from
  outside the fabric, with no BGP session and no security group involved.

The placement is what keeps the platform working: the responder's upstream
DNS and the metadata proxy's relays are the *namespace's* own traffic
(OUTPUT), while everything a guest sends is transit (FORWARD). What a guest
legitimately needs from the installation it still gets through the `proxy`
NF, exactly as this decision already said it should.

**The consequence to plan for.** A guest can no longer reach *any* internal
address directly, and that includes ones the platform itself used to hand
out. A managed realm is the case to check before this ships: its nested core
is configured with `ecosystem_endpoint` pointing at the parent's
`ecosystem_api_url`, and a realm node is a guest of the parent overlay — so
that call is transit through the parent's egress namespace and is now
dropped. The answer is not a hole in the guard but a door through the proxy:
the endpoint belongs in the `proxy` function's `ports`/`forwards` beside the
boot, orchestration and status APIs, which is where every other thing a
guest needs from the installation already goes. An allow-list of internal
prefixes is the other option and a worse one — it is a hole with a reason
attached, and reasons outlive the thing that needed them.

Not covered, and deliberately: a hypervisor of the fabric can still inject
anything into any VNI. The fabric trusts its own members, and the honest
answer for a fabric whose underlay is not trusted is per-VTEP IPsec (see
Non-goals) or a `tun_src` match against the peer list.

**What interrupts a VNI's egress.** Worth knowing before an edit, because
neither reads like a data-plane operation: changing a subnet's **gateway**
or its **MTU** rebuilds the namespace's wiring whole (`_ensure_vni_egress`
tears down and rebuilds — the veth ends still inside it would collide with
a half-rebuild), so every guest of that VNI on that host loses egress and
its conntrack for the moment it takes. Everything else about a subnet is
carried without touching the path. The address the fabric speaks from is
deliberately *not* in this list: it is sticky, and only a lost address
moves it (see decision 9), because a route-table twitch would otherwise
restart gobgpd and the forwarder under every guest on the host.

The per-host shape is a **per-VRF SNAT
namespace** (the same per-VRF-netns pattern the `proxy` NF uses) holding
the gateway leg `172.16.0.254/24` plugged into OVS plus a transfer veth
to the root namespace; masquerade inside the namespace, root masquerades
the transfer net into the uplink. Namespaces keep overlapping VRF CIDRs
and their conntrack apart with no zone gymnastics. Two hard requirements
found for the `ovs_evpn` builder:

- the VRF needs an explicit **default route toward the leg** — the L3
  pipeline forwards only announced prefixes, a guest's `default via`
  alone compiles to nothing;
- the leg's routes (`.254/32`, `0.0.0.0/0`) must be announced with a
  **non-imported route target**: `evpn_connector` turns a prefix present
  both locally and remotely into an ECMP group, so an exported egress
  leg would hairpin part of the traffic through other hypervisors —
  no-export is what keeps DVR egress strictly node-local.

In the agent driver (`gcl_sdk/agents/universal/drivers/evpn/`): `EvpnPort._apply` calls
`_ensure_vni_egress` whenever the CP subnet carries a gateway — the `via`
of its **default** route, which is also the one the responder hands the
guest, and reading that list two different ways is how the guest came to be
told one address while the host held another — one namespace per VNI per
host, the
gateway leg plugged into the EVPN bridge with its own client config
(gateway `/32` + `0.0.0.0/0`, host-unique non-imported `exp_rt` derived
from the hostname), a per-VNI transfer `/30` allocated from a dedicated
base range, and the double masquerade. Torn down with the last guest of
the VNI on the host (`_gc_vni_egress`).

A per-realm option **"stable egress IP"** is planned (post-increment-1): the
realm VRF's default route points at an edge node, which SNATs into one stable
address — the same `SnatRule` mechanism as Step 0, at the cost of
hairpinning.

### 7. Ingress: realm LBaaS instances, target delivery via routed anycast VIP

The core node's central LB serves core services only. Realm/tenant ingress
goes through **LBaaS instances**. Target traffic delivery to an LBaaS
instance inside a VRF is a **routed public VIP without NAT**: LB nodes hold
the VIP on loopback and announce it as an anycast Type-5 route
(`ClientEdgePrefixAnycast`, health-checked); edge nodes just route. This
preserves the client IP, provides HA/ECMP without keepalived, and — unlike
DNAT on the edge — is compatible with distributed egress (edge DNAT plus DVR
return path is asymmetric and breaks conntrack).

The Step 0 border `Forward` (DNAT) remains as a transitional mechanism for
non-LB `(ip, port)` forwards. Caveat: DNAT on the edge is only correct
when the target VRF's default route points at that same edge node
(stable-egress mode) — under distributed egress the reply leaves through
the local hypervisor's SNAT and bypasses the edge's NAT state, breaking
the connection regardless of how many edge nodes exist.

Public addresses are not a separate resource type: they are ordinary
allocations in a **public network** — an ordinary `Network` whose prefix
is routed from outside, shared to consumers via its `access` kind (see
the control-plane API below). MTU note: routed ingress traffic enters the
overlay with full-size frames, so edge/LB nodes apply TCP MSS clamping
(or rely on PMTUD) unless the underlay carries jumbo frames.

### 8. BGP fabric: route reflector on the core node; loss of RR must not break the data plane

All hosts peer `l2vpn-evpn` with a gobgp **route reflector** (capability
`bgp_rr`) on the core node; no full mesh (per-node config stays static,
adding a node touches nobody else).

Availability requirements:

- **LLGR (long-lived graceful restart) with a large retain time (e.g. 24 h)
  on all sessions from day one**: RR loss marks routes stale instead of
  withdrawing them — existing connectivity keeps working (fail-static), only
  convergence of changes pauses.
- **Second RR** as soon as the installation has a second suitable node.
- Mandatory increment 1 test: kill the RR and verify OVS flows survive.
  `evpn_connector` reconciles flows from the local gobgp RIB (`ListPath`), so
  stale-path visibility must be confirmed; if stale paths disappear from the
  RIB, `evpn_connector` gets an explicit fail-static mode.

  **Measured: fail-static does not hold out of the box, so the
  `evpn_connector` mode is required, not optional.** On a 3-node stand (RR + 2 hypervisors,
  gobgp 3.34) killing the RR withdrew the reflected `/32`s from each
  hypervisor's RIB within ~10 s (5 → 3 prefixes); `evpn_connector`
  dutifully removed the corresponding OVS flows (17 → 15) and cross-host
  connectivity was lost until the RR returned. Two compounding causes:
  (1) LLGR never negotiated — only base graceful-restart (120 s) showed
  in the capabilities despite LLGR config on both ends; (2) even the GR
  helper did not retain, because `systemctl stop gobgpd` sends a
  NOTIFICATION and, without RFC 8538, that flushes routes immediately.
  A real RR crash (holdtime expiry) is gentler than an admin stop, but
  relying on that distinction is exactly the fragility the fail-static
  mode removes: `evpn_connector` must keep the last-known-good flows when
  paths vanish due to session loss, independent of BGP-layer retention.

  Implemented in `evpn_connector` (`[gobgp] fail_static`, default on): while any configured BGP peer is
  not ESTABLISHED, the connector unions the freshly computed flow set
  (local changes keep applying) with the last snapshot taken when all
  peers were healthy, so `replace-flows` cannot delete routes that
  vanished with the session. Rerun of the spike test: a 2-minute RR
  outage kept flows (17→17) and guest connectivity up throughout, the
  mode released cleanly on RR return, and deletions still propagate
  while peers are healthy (no freeze).

Security: the DVR SNAT leak (decision 6) gives realm VMs a TCP path to the
RR sourced from a legitimate hypervisor uplink address, i.e. a chance to
impersonate a peer and inject Type-5 routes into foreign VRFs. BGP (179)
and gobgp gRPC ports MUST be blocked in the leak; sessions SHOULD use
TCP MD5/AO on top.

**bgp_rr implemented and validated live**: the CP driver maintains a
`bgp_rr` target resource for the configured RR agent
(`[evpn] rr_agent` / `rr_peer_prefixes`), and the host driver renders a
reflector config with **passive peering via gobgp dynamic-neighbors**
over the underlay prefixes (RR-client peer group, GR+LLGR) — adding a
hypervisor touches nothing on the RR. On the spike stand the rendered
config replaced the static one and both hypervisors re-established as
dynamic peers. Note the dynamic-neighbor prefix list is itself a peer
filter — one more reason the SNAT leak must not expose port 179.

**One node may hold both fabric roles.** In the single-host
stand the core node is its own reflector *and* a hypervisor, and gobgpd
reads exactly one file — so each role writes a fragment under
`/etc/gobgp.d/<kind>.json` (its global section and its own body) and
`/etc/gobgp.conf` is assembled from every fragment present: one identity
(the reflector's, since `bgp_rr` sorts first) plus both roles' sections.
Before this, `evpn_host` and `bgp_rr` overwrote each other's config and
restarted the daemon on every iteration, for ever. Removing one role
reassembles what is left and only the last one out stops gobgpd. A host
whose configured RR address is its own is not given a session to itself
(it never comes up; its own gobgpd already holds the RIB its
`evpn_connector` reads), which is what makes the deployment rule
"control node ≠ workload hypervisor" a preference rather than a
constraint.

### 9. Config delivery: universal-agent capabilities, no shared directory

The delivery path proven by Step 0 is used directly (no interim shared-dir
mechanism):

- kind **`evpn_node`** on every hypervisor agent: per-host set of
  `evpn_connector` client JSONs (mac, `/32` routes, VNI, RT), host-local DHCP
  records, and gobgpd session parameters (source IP, RR addresses);
- kind **`bgp_rr`** on the core node agent: reflector config (LLGR, passive
  peering).

The new network driver **`ovs_evpn`** (`gcn_network_driver` entry point) is a
pure *computer* of these target resources from `Network`/`Subnet`/`Port`
models (same builder pattern as border/LB); it never touches hosts itself.

**Guest wiring is scheduled to the hypervisor**: a guest VM
plugs into its *hypervisor's* br-int, so `evpn_port`/`evpn_host` target
resources go to the hypervisor node's agent, not to the guest.
`MachinePool.hypervisor_node` (set by
`exordos compute hypervisors register-agent --pool`) names that node; the
driver resolves `port.node → Machine → pool → hypervisor_node` and defers
emission until the guest's machine is placed on a pool. A pool without
the mapping keeps the legacy behaviour (the node wires itself — the
self-hosted-fabric path used by the spike).

**What "actual" means on the host**: the driver reports what
is *installed*, not what it once wrote — it reads the host once per
iteration (both bridges' ports, br-int's flow cookies, every port's VLAN
tag, the guests' `external_ids`, the namespaces, and the state of the
units it owns) and each resource checks itself against that snapshot.
Two rules keep the check from becoming its own failure mode:

- **Nothing is reported missing on the strength of an unknown.** A fact
  the host could not be asked about (no systemd, an EVPN bridge that
  evpn_connector has not created yet, a guest whose VM is switched off)
  is not drift; a false "missing" recreates the resource on every
  iteration, for ever, which is worse than a drift that goes unnoticed.
- **Every check has a repair in `apply`.** A resource that reports "my
  daemon is down" is reinstalled by the agent, so the apply path starts a
  dead daemon even when it finds its configuration unchanged; an egress
  namespace that lost its leg is torn down and rebuilt rather than
  patched, because the veths inside it would collide with new ones.

**OpenFlow 1.3 on br-int**: every `ovs-ofctl` call the agent
makes names the version, through one wrapper, so no call site can forget it
and silently negotiate 1.0 — the version the other half of the data plane
(evpn_connector's bridge) already speaks. The bridge itself keeps OVS's
permissive protocol list: pinning br-int the way the EVPN bridge is pinned
would make a plain `ovs-ofctl dump-flows br-int` fail, and br-int is the
bridge an operator debugs by hand. Nothing in the flow set had to change —
the conntrack pipeline, the intercepts, the bundles and the cookie-scoped
deletes all work over 1.3 — what it buys is meters and group tables, which
the rate limiting and anycast ingress on the roadmap need.

**`ofport` is a host-side fact**: VM interfaces are plugged into OVS via
libvirt (`virtualport type='openvswitch'`), the agent reports the actual
ofport back, and the on-host driver substitutes it into client configs. The
control plane manages only mac/ip/VNI. The compute libvirt driver emits that
`virtualport` with `interfaceid=<port uuid>` when the pool is OVS-backed
(`ovs` spec flag), so libvirt plugs the tap into `br-int` and stamps
`external_ids:iface-id` — the key `EvpnPort._resolve_guest_port` looks the
guest up by (validated live: the same tap is found by `iface-id`, with
`attached-mac` as the fallback).

### 10. Recursive model: every level runs its own independent SDN

A realm is a full installation and runs its **own** SDN over the VRF network
its parent gave it (the parent's overlay is the child's underlay; the
child's VTEPs are its node VMs' addresses inside the parent VRF). VNI/RT
spaces of different levels are disjoint universes — **no cross-level
coordination or allocation hand-off exists**. Each core allocates VNI/RT
automatically from its own `Ipam` when a `Network` with the `ovs_evpn` driver
is created. The only thing a parent provides to a realm is its VRF network,
created by the ecosystem realm builder.

### 11. MTU is a first-class field

The single cross-level contract of the recursion is the MTU budget: each
nesting level costs ~50 bytes of VXLAN encapsulation. `Network`/`Subnet`
carry an explicit MTU; the overlay MTU is derived as underlay MTU − 50 and
handed to guests via the DHCP `interface-mtu` option. Jumbo frames (≥1600,
ideally 9000) on physical underlays are the recommendation that makes the
first nesting levels free.

### 12. Packaging: SDN stack is an optional part of the core element

OVS, gobgpd, `evpn_connector` and the DHCP responder are **not** baked into
the base image and are **not** a separate element: they ship as an optional
part of the **core element**, installed onto live nodes through the standard
element mechanism, with a CLI command to enable/disable the role on a node.
SDN versions are therefore coupled to the core release, which also pins
`ovs_evpn`-driver ↔ `evpn_connector` compatibility. Requirements that follow:
artifact availability through the repo chain at every nesting level, and an
idempotent, upgradeable installation.

### 13. Coexistence and end state: everything on OVS eventually

Increment 1 keeps two drivers side by side: realm networks on `ovs_evpn`
(the realm builder creates one network per realm), infrastructure networks on
`flat_bridge`. The target end state is a single OVS data plane; migrating
infrastructure networks to OVS is a roadmap phase (the boot network stays
semantically flat-on-underlay regardless of bridge technology). Existing
realms are migrated by recreation; live flat→EVPN migration is out of scope.

## Control-plane API

The user-facing model for network management. Guiding principles:

- **Thin port.** A port is a NIC: an attachment point with a MAC, an entry
  into the processing graph, and a list of addresses. It never accumulates
  per-feature fields.
- **Network is topology, not type.** There is no "external" network type
  and no separate PublicIP pool: a public network is an ordinary network
  whose prefix is routed from outside; public addresses are ordinary
  subnet allocations in it (Neutron reached the same model the hard way).
- **Traffic processing is a graph of network functions.** Firewalling,
  DHCP, VIP/FIP and future services are reusable typed nodes connected
  into a DAG, compiled into flat rulesets for speed.
- Lessons adopted from OVN/Neutron: constrained match schema instead of a
  user-facing expression language; no rule priorities in the user API
  (ordered rules + default); group-centric compilation (shared policy is
  compiled once and referenced, member address sets are derived by the
  builder); DHCP answered host-locally from CP facts.

### Resource tree

```text
/v1/network/
├── networks/                    CRUD; driver/access/egress kinds
│   └── <uuid>/subnets/          CRUD (nested; subnet is meaningless alone)
├── ports/                       CRUD + actions/attach|detach
├── nfs/                         CRUD; network functions, edges inline
├── lb/…                         unchanged (converges with an `lb` NF kind)
└── border/                      unchanged (Step 0, transitional)
```

CLI mirrors the tree: `exordos network networks|subnets|ports|nfs …`.

### Network

Fields: `name`, `project_id`, `status` (RO) and three polymorphic kinds:

- `driver`: `flat_bridge` | `ovs_evpn {mtu, vni (RO), rt (RO)}` — VNI/RT
  are allocated by the core on creation and only read through the API;
- ~~`egress`: `dvr` | `stable`~~ — **not built.** DVR is the only egress
  model the data plane implements, so the field is deliberately absent
  from the API: a knob whose other value silently means the same thing is
  worse than no knob. The stable-egress roadmap item stands (see below);
  the field arrives with it;
- `access`: `private` (default: owner project only) | `public` (any
  project of the installation may allocate addresses) | `projects`
  (roadmap: explicit grant list).

`access` semantics — what `public` grants to foreign projects: seeing the
network and its subnets in listings and allocating addresses in them
(port addresses, `fip` references). What it does not grant: managing the
network/subnets, seeing other projects' ports/allocations, or routing
into the network (inter-network routing is a separate right, never
implied by shared access). Publishing a network
(`access` → `public`) requires a dedicated `network.network.share` permission.

Explicit caveat: **the isolation boundary is the network, not the
project**. Projects allocating addresses in one shared network are
mutually routable inside it; per-port protection is the NF graph.
Per-project allocation quotas in shared networks are a roadmap item.

### Subnet

Nested under a network: `cidr`, `ip_range`, `dhcp`, `routers`,
`dns_servers`; infrastructure fields (`next_server`,
`ip_discovery_range`) are valid only in `flat_bridge` networks. MTU lives
on the network's driver kind (decision 11).

### Guest-visible semantics of `ovs_evpn` networks

The overlay is L3-only, and guests can tell. The contract:

- No broadcast or multicast crosses hosts — there is no BUM in the
  fabric (decision 2). Same-subnet VM↔VM traffic is routed `/32`; ARP is
  answered by the local host (proxy).
- Consequently guest-level VRRP/keepalived, mDNS and L2-multicast
  clustering protocols do not work between VMs on different hosts, and
  the platform has no floating service address to offer instead — the
  `vip` function that would have been it is gone (see the NF section).
  A service that needs one is a service that needs a balancer.
- DHCP and DNS are served host-locally (`dhcp`/`dns` NFs); the netboot
  and metadata endpoints are host-local too (`proxy`).
- Workloads that genuinely need stretched L2 are the use case for the
  future EVPN Type-2 segments option (decision 2), not for creative
  workarounds.

### Port

A port belongs to a node, not to a network:

```json
{
  "node": "<uuid>",
  "mac": "…",
  "nf": "<entry NF or null>",
  "port_security": true,
  "addresses": [{"subnet": "<uuid>", "address": "10.42.0.7"}]
}
```

- `addresses` — inline list of exclusive IPAM allocations (usually one).
  An address anchors the port into the subnet's network: its `/32` Type-5
  route goes into that VRF. Ingress is classified by destination address,
  egress by source address, so multi-homing one NIC into several networks
  (e.g. private + public) is just a second list entry. An empty list is
  valid (only punt NFs work). Moving a port between subnets/networks is
  an edit of this list. `address` omitted ⇒ allocated by IPAM.
- `actions/attach {node}` / `actions/detach` — same convention as volume
  attach; `machine` is derived and read-only. Ports are auto-created with
  machines (boot NIC) exactly as today.
- `port_security` — anti-spoof: only the port's own MAC and its own
  address may be sourced. A `fip` needs no exception: the NAT is on the
  hypervisor, so what the guest sends is its private address either way.
- Listing filters (`?network=&subnet=&node=&machine=`) resolve through
  `addresses`.

### The address ledger

An address a caller reserved is one row in `net_addresses` — the
DB-backed source of truth for IPAM, beside the older in-memory `Ipam`
that scans ports and still serves the flat networks:

```text
net_addresses: uuid, project_id, subnet, address,
               allocation: reserved | freed,
               origin: auto | explicit | floating,
               owner_port, association,
               UNIQUE (subnet, address) WHERE allocation = 'reserved'
```

Two facts, deliberately separate, and this is the Elastic-IP shape:
**`allocation`** says whether the address is held against its subnet's
pool, and **`association`** says which port is using it right now. So a
public address survives the machine that answered on it — disassociating
does not release, and releasing is a thing you do on purpose.

Each half is enforced, which is the part that took a second pass to get
right. The allocator counts the reserved rows and only those, so freeing
really does hand an address back; the uniqueness that stops two live
claims on one address is scoped to those rows too, or a freed one would
keep its address out of circulation for ever. Freeing is refused while
the address is associated or while it belongs to the port it was
allocated for — the same two facts that refuse a delete, because freeing
*is* the delete that keeps the receipt. Re-reserving re-checks
availability: the address may have gone to somebody else in between.

`association` is written by the compiler, not by the caller's intent: a
port that names a public address gets recorded as its user, and a second
port naming the same one is refused at 400 rather than quietly
programming a second NAT for the same public IP on another hypervisor. A
port that stops using one gives it back, and that has to happen on the
compile and not only on the port's delete: unbinding leaves the port in
place, so nothing else would ever clear the pointer, and an address
marked as used by a port that stopped using it can be neither re-cited
nor released. A port that goes away releases outright — deleted if the
port owned it (`floating_from` allocated it), disassociated if it merely
cited it, because that one is the caller's and outlives the port.

Auto-allocation takes a `SELECT FOR UPDATE` on the subnet row before it
scans, so two concurrent allocations serialize rather than race to the
unique index. Explicit-address policy: auto-allocation is always allowed;
picking a specific address in your own network is allowed; in a shared
network it needs the `network.address.explicit_address` permission (owner
and admin by default) — no squatting on pretty public addresses.

### Network functions

**dhcp, dns and proxy are functions too.** They were the
compiler's secret — a descriptor invented per port out of `[evpn]` options
and the subnet's flags, so nobody could list them, see what reached a host,
or give one network different resolvers from another without changing the
installation's configuration. They are kinds now, with config schemas that
fail at 400 rather than compiling into a data plane that quietly does
nothing: DHCP belongs to the subnet (every value it hands out is the
subnet's), the resolver and the netboot/metadata proxy to the network.

Zero-config is preserved by *seeding* rather than by inventing: the first
compile of a subnet creates the three functions with the installation's
defaults as their initial config and never rewrites one that already
exists. A seeded default is an object the caller can edit, and an edit
makes it theirs (`provenance` flips to `user`). Read-only stays where it
belongs — a `simple` port's expansions are still the port's to change,
because editing them directly would be compiled away on the next pass.

**A port arrives filtered.** Every subnet is also seeded a default
`splitter`: everything out, and in only from the subnet the guest is on —
the shape AWS gives a default security group, read for a model where the
group is the subnet. An empty rule list is no longer a policy: a port that
names no group of its own gets that default, attaching a group replaces it
(not widens it), and an installation that deletes the object gets ports
that are genuinely unfiltered and say so. The platform's own services are
unaffected, being intercepted above the filter.

An NF is a typed processing node, and it exists because something that
owns it does: a subnet's DHCP and its default group, a network's resolver
and metadata proxy, what a port expands into. There is no way to make one
on its own, because there would be nothing to read it.

> **The graph was designed and is not built.** NFs were to carry named
> `outputs` — edges to the next function or a terminal (`forward` /
> `drop` / `reject`) — with a `custom` port kind naming the entry
> function, so a port's treatment could be an explicit DAG. It was built
> and then removed: every real port is a `simple` one, the compiler read
> the graph for exactly three kinds, no CLI could create such a port, and
> the terminals never reached the data plane at all — the agent's
> contract is an allow-list, not a graph. What is left is what the
> compiler actually compiles. If inter-VRF routing or an L4 balancer ever
> needs a graph, it comes back with the thing that needs it.

Each element intercepts the traffic it is responsible for. The kinds:

- **`splitter`** — the filter: an allow-list of `rules`
  (`{direction, protocol, port?, remote_ip?|remote_group?}`), and
  nothing else. It carried a `default` output and a `stateful` flag once;
  both were removed, because the pipeline it compiles to is a conntrack
  one and always was — established is allowed and the rest is dropped, so
  a knob whose other value nothing implements is worse than no knob. A
  security group is a splitter pattern, not a separate resource. The
  shape is **codified and validated live**: an
  `evpn_port.security_rules` allowlist (`{proto, port?, dst?}`) compiles
  in `EvpnPort` to a conntrack pipeline on `br-int` — the guest's
  untracked IP egress goes to `ct(zone=<vlan>)` (per-VNI zone, since realm
  CIDRs overlap), established is allowed, each rule commits + returns to
  NORMAL, everything else drops. Scoped by the guest's `in_port`; the
  DHCP/DNS/proxy punts sit above the entry so infra bypasses the filter;
  GC'd by a per-port cookie.
- **`dhcp`** — punts DHCP requests to the host-local responder answering
  from Port/Subnet facts (decision 4); config is a slot for extra
  options and netboot parameters (a `filename` HTTP URL pointing at the
  `proxy` NF).
- **`proxy`** — punts HTTP to the cloud-init well-known address
  (`169.254.169.254`) into a host-local proxy serving an **allowlist of
  core endpoints**: netboot scripts and artifacts (via the agent's repo
  cache), Seed/universal-agent CP calls, cloud-init-style metadata later.
  Terminated on the kernel stack behind per-VNI `md<vlan>` ports, folded
  into the one NF process (see the proxy paragraph below). Per-machine
  identity injection (signing the requesting port) is a follow-up; the
  device-bound listener already isolates the request to its VNI. One NF
  covers what would otherwise be separate `boot` and `metadata` kinds.
- **`dns`** — punts port 53 (`udp`/`tcp`) on `br-int` into the same merged
  host-local responder: answers the installation's internal zone from CP
  facts (a port's node name → `A` + reverse `PTR`) and forwards everything
  else through the installation's upstream resolvers — private and public
  resolution for every VM with no runtime dependency on a central resolver
  (OVN answers DNS on the hypervisor the same way). Guests use the
  **gateway as their resolver** (handed out via `dhcp` option 6); the punt
  intercepts it before the EVPN patch, and the responder scopes the zone
  by the guest's recorded VLAN. Config: `zones` (served domains), extra
  records, forwarders. Naming scheme and zone sources: see the DNS
  section. Not on the checkpoint critical path (upstream resolution via
  egress suffices meanwhile).
- **`fip`** — 1:1 NAT between a public-network address and the port's
  own address: guest-unaware public identity in both directions,
  strictly one port. NAT happens on the port's hypervisor (path stays
  symmetric with DVR). A port asks for one through its own `public`
  slot; the function is what the compiler publishes so the substrate
  stays inspectable.

> **`vip` was designed, built and removed.** A shared address several
> ports answer for, announced Type-5 by each of them (anycast/ECMP
> fan-in). Two things made it a promise rather than a feature: it had no
> `health_check`, so the announce was unconditional and a dead backend
> kept its share of the traffic; and it was reachable only through the
> graph above, which nothing could create. The addresses it would have
> claimed are the address ledger's, and the fan-in it would have built is
> what an L4 balancer is for.

So there are two address flavours, not three: a **port address** is
exclusive and unconditional — the port's identity; a **`fip`** is NAT,
and the guest keeps its private addressing.

> `dhcp`, `dns` and `proxy` were briefly *not* NF kinds — the compiler
> derived them, on the grounds that they carried no configuration a caller
> supplies. That was reversed once they did: a resolver's forwarders, a
> zone suffix, a netboot filename and the proxy's grants are all things an
> installation sets per network. See "dhcp, dns and proxy are functions
> too" above for the shape as built.

Networks themselves are NFs in the limit: an edge from an NF output into
another network is inter-VRF routing built from the same constructor.
This is deliberately out of scope for increment 1; when it lands, such an
edge requires rights on the target network.

### Why the NF flows are shaped this way

Each of these is in the driver because its absence was a defect, and each
reads like an arbitrary choice until you know which one
(`gcl_sdk.../drivers/evpn/flows.py`):

- **Per guest, on its own `in_port`.** Switching a function off for one port
  then really switches it off — its DHCP and DNS take the ordinary path to
  whatever server the guest chose, instead of being stolen by a responder
  that has no record for it and dropped into silence.
- **Above the security-group pipeline.** A function *intercepts*: it takes
  the traffic it serves before anything downstream classifies it, so a
  default-deny group never has to carve out an exception for the guest's own
  lease or resolver.
- **... and therefore carrying their own source match.** Sitting above the
  port-security drop is what makes them the way around it otherwise: the
  metadata proxy decides who is asking from the connection's source address,
  so a guest that can be intercepted while claiming a neighbour's address can
  ask the platform about that neighbour. DHCP is the exception it has to be —
  a guest without a lease has no address to be held to yet.
- **An explicit return leg.** What the responder sends is a *new* connection
  as far as the guest is concerned (the request it answers never traversed
  conntrack, having been intercepted), so the answer has to leave at
  intercept level too, or a default-deny ingress list drops the guest's own
  DNS replies. Being per guest and explicit, it also needs no VLAN and cannot
  deliver one tenant's reply to another's port.
- **Delete and re-add as one bundle.** A reconcile must leave no window in
  which a guest's DHCP escapes uninterrupted, nor one in which its answers
  cannot get back.

### Why the security group is shaped this way

- **The ingress entry matches the delivery ofport, not the MAC alone.** An
  unscoped ingress entry outranks (priority 91 > 90) the *egress* entry of
  whoever sends the packet, so a guest could skip its own allow-list simply
  by addressing a local neighbour that has a group attached.
- **`port_security` is enforced on the bridge, not at the tap.** libvirt
  refuses a domain carrying both an nwfilter and an openvswitch virtualport,
  so every overlay port had the toggle and none of the behaviour. On the
  bridge it pins every entry into the pipeline to the guest's MAC and to the
  addresses the control plane gave it — which is also what keeps an ingress
  rule that names a peer *by address* from being satisfied by a neighbour
  that simply chose that address.
- **One OpenFlow bundle.** A del-flows followed by N add-flow calls leaves a
  window — after the delete, before the entry flow lands back — where the
  guest matches no SG flow at all and falls through to br-int's default
  NORMAL: briefly unfiltered.
- **Infrastructure is exempt**, as it is on AWS: the DHCP/DNS/metadata
  intercepts sit at priority >= 100, above the pipeline, so a default-deny
  group never cuts a guest off from its own lease and resolver.

### Dataplane ownership: two bridges

`evpn_connector` is a **third-party dependency** and stays one: it
provides EVPN/VXLAN forwarding and nothing else. It owns its bridge
(`evpn`) whole — it rewrites the entire flow table atomically every sync
(`replace-flows`), so **nothing else may write flows on that bridge**.
We therefore do **not** put NF logic into `evpn_connector`; the NF layer
lives on a **separate, agent-owned bridge**, and the two are joined by an
OVS **patch-port** pair. This is the OVN `br-int` + `br-tun` split:

```text
guest tap ─▶ br-int (agent-owned: NF graph, splitter, dhcp/dns/proxy)
                │  forward terminal
                ▼  patch-port
             evpn  (evpn_connector-owned: EVPN Type-5, VXLAN)  ─▶ underlay
```

- **`evpn` bridge — 100% `evpn_connector`.** Unmodified external
  dependency. Its client config lists the **patch-port** as the local
  port for a VNI; it resolves that ofport and does EVPN as usual.
- **`br-int` — 100% the agent** (the `evpn_node` capability driver).
  Guest interfaces plug in here; the agent owns all NF flows and the
  host-local responder daemons (`dhcp`/`dns`/`proxy`), with no
  `replace-flows` conflict since it is the sole writer of this bridge.
  The `forward` terminal hands a packet through the patch into `evpn`
  with the VNI context; return traffic comes back through the patch.

There is **one patch-port pair per guest** per host (`pi<vlan>`/`pe<vlan>`,
keyed by the guest's own local VLAN): the `evpn_connector` client config
points its `ofport` at that guest's patch, so `evpn_connector` announces
the guest's `/32` and routes between guests over their distinct patches,
and `br-int` runs the NF graph. It is per **guest**, not per VNI, because
`evpn_connector` rejects two clients that share an `ofport` — it cannot
hairpin traffic back out its ingress port (validated live: a second guest
sharing a per-VNI patch ofport was silently ignored, so only one guest per
VNI worked; giving each guest its own patch fixed guest↔guest within a
VNI). Each guest therefore also gets its own br-int VLAN, so a shared VLAN
never flaps `router_mac` across sibling patches.

Measured: moving a guest off the `evpn` bridge onto a fresh `br-int`
joined by a patch pair, with its client config's `ofport` repointed at the
patch, keeps cross-host connectivity in both directions **with
`evpn_connector` neither modified nor paused** — the composition holds.

Owning `br-int` also **simplifies the punt NFs dramatically**. On the
`evpn` bridge (owned by `evpn_connector`) a DHCP/DNS/proxy responder
needed an explicit punt flow, a dst-MAC rewrite (guests target the
ARP-proxy router MAC) and a static neighbour, plus pausing the forwarder
(`replace-flows`). On `br-int` (a NORMAL bridge the agent owns) **one
merged per-host DHCP+DNS responder** serves the whole hypervisor from a
single trunk internal port (`br-int-nf`, no access tag ⇒ carries every
VNI's VLAN) — to keep the on-host SDN footprint minimal: one process, one
port, one systemd unit. It is a raw `AF_PACKET` socket, **no OpenFlow
controller / os-ken dependency**.

*DHCP* is broadcast: standard L2 floods each guest's `DISCOVER` to the
trunk port and the responder replies tagged back into the same VLAN,
recovering the VNI context from `PACKET_AUXDATA` (OVS strips the 802.1Q
tag on receive, so the VLAN arrives in the same ancillary data tcpdump
uses to print `vlan N`). It holds **no gateway IP itself**; it crafts each
reply from the client's control-plane record (keyed by the globally unique
MAC), so overlapping VNI CIDRs never collide on one process. It serves
**only static `mac→ip`** plus a few options (mask, router, DNS, MTU) —
strictly less than dnsmasq: no lease DB, no dynamic allocation, no TFTP.

*DNS* is unicast to the gateway, which `evpn_connector`'s catch-all ARP
proxy already answers (`router_mac`) — no resolver IP could escape that,
so the guest cannot reach an NF address directly. Instead the responder
hands out the **gateway itself as the resolver** (DHCP option 6), and two
priority-100 flows on `br-int` **punt `udp`/`tcp` port 53 to the trunk
port before NORMAL forwards it to the EVPN patch** (the OVN/Neutron
"distributed-DNS" pattern, but on the bridge *we* own — `evpn_connector`'s
bridge stays untouched). The responder echoes the query's destination as
the reply source and scopes the internal zone by the **guest's own VLAN
taken from its record** (keyed by the guest MAC), because OVS drops the
802.1Q tag on an OpenFlow punt so `PACKET_AUXDATA` is unreliable there —
overlapping realm names therefore never collide. Internal `A`/`PTR` come
from the per-port records (`name`→`ipv4` + reverse); everything else is
relayed to the installation's upstream forwarders. **Spike-validated then
codified**: on the live stand a guest completed `DISCOVER→OFFER→REQUEST→
ACK`, bound its CP address + MTU, and resolved an internal name, its
reverse `PTR` and an upstream name — all through the one process with
`evpn_connector` active and unmodified. The same path runs as the
`exordos-evpn-nf` systemd unit ensured by the per-host `evpn_host` model
(`_ensure_nf_responder` + `_ensure_dns_punt_flows`), with the responder
shipped as the `gcl_sdk...drivers.evpn_nf` module.

*Netboot/metadata proxy (наливка).* Unlike DHCP/DNS (stateless UDP crafted
over a raw socket), the proxy is **stateful TCP/HTTP**, so it uses the
kernel stack. Each VNI gets a **tagged-access** internal port `md<vlan>`
holding the well-known cloud-init address (`169.254.169.254`) behind a
fixed MAC; the tag gives the kernel a per-VNI identity in both directions.
A **per-guest** punt flow on `br-int` — matched on the guest's `in_port`
(access-port ingress is untagged, so `dl_vlan` cannot match) — rewrites the
dst MAC to that fixed MAC (guests address evpn_connector's router MAC,
which the kernel would drop) and outputs to the guest's `md<vlan>`. The
proxy is folded into the **same one process** as DHCP/DNS: a daemon thread
opens one HTTP listener **per `md` interface via `SO_BINDTODEVICE`**, so
two realms that reuse a guest IP land on different sockets and never
collide, and a per-VNI policy route (`oif md<vlan> → table`) sends each
device-bound reply back out the right port. It serves netboot artifacts
from a local cache and an allowlist of upstream prefixes; everything else
is 404 (never an open relay). **Validated live incl. the overlap case**:
two guests in different VNIs both `10.42.0.1` each fetched their netboot
from the one process; the per-guest punt carries a cookie so `evpn_port`
deletion GCs its own flow. Codified in `EvpnPort._ensure_metadata_punt` /
`_ensure_vni_metadata`, shipped as `gcl_sdk...drivers.evpn_proxy`.

**What the proxy will answer, and for whom.** This is the one
door an overlay guest has onto the platform, and what is behind it — the
boot API and the orchestration/status APIs — answers whoever can reach it:
the boot API mounts no authentication at all (that was always a netboot
service's bargain, and it used to be unreachable from a tenant's network).
So the gate here is the whole of the access control, and it is an
**allow-list**, not a refusal of what looks wrong. The rule it replaced —
"a request that names a machine must name this one" — was a *shape*, and a
shape only covers the requests that have it: every path with no uuid in it
went through unexamined, so a guest could register agents (`POST
/v1/agents/`) and write any node's reported state (`POST
/v1/kind/<kind>/resources/`). Naming somebody else was refused; naming
nobody was not.

Three cases, with nothing between them:

- a **published subtree** — the local netboot cache, or an artifact prefix
  the installation granted — is static files, identical for every guest,
  and any of them may *read* it. Consequence for configuration: a grant
  narrower than `/` means "a subtree published for guests", so an API must
  never be granted under one;
- a **machine-scoped** path (`/v1/boots/`, `/v1/agents/`, `/v1/nodes/`,
  `/v1/node_verifiers/`) names a machine and must name the caller. Checked
  against the path alone, because the client may be an iPXE ROM that sends
  the GET and nothing else — and against the query too, which the gate used
  to ignore while `_forward` passed it upstream untouched;
- everything else behind a platform grant must **claim to be** the caller:
  the `X-Genesis-Node-UUID` header, or `uuid`/`node` in the body. The claim
  decides nothing on its own — who is asking is settled by the source
  address, which the port's own flows make unforgeable — it only has to
  agree with it.

The claim is what an agent already sends: encrypted clients set the header,
and the unencrypted boot-API client's objects carry `node`. That also fixes
the mirror-image bug in the old rule: a *resource* is named by a uuid of its
own, so holding it to the caller's refused an agent its own reports while
proving nothing. What stays refused is a resource **read** by an
unencrypted client — the path says nothing and there is no body to say it
either; the client sending the header is the fix, not a weaker gate.

**Isolation on `br-int`.** A flat NORMAL bridge is one L2 domain, so guest
ports (with possibly overlapping CIDRs) must be separated by a **per-guest
local VLAN tag** on the guest port and its patch — the OVN internal-VLAN
pattern, one guest per VLAN. The trunk NF port carries all of them and
demultiplexes by the packet's VLAN (DHCP) or the guest MAC's recorded
VLAN (DNS), never by a per-VNI process. The port→local-VLAN map is per-host
ephemeral and is also written into each guest's NF record so the responder
can scope DNS. Because every guest owns its patch, VLAN and `md<vlan>`
port, `EvpnPort.delete_from_dp` garbage-collects that guest's dataplane on
its own delete — the patch pair, the `md<vlan>` port with its return route
and policy rule, and the local-VLAN reservation — with no "last port of
the VNI" bookkeeping (validated live).

The only change we carry in `evpn_connector` is **fail-static** — a
general robustness fix (retain flows while a BGP peer is down) that is
not exordos-specific. It currently lives only on our `evpn_connector`
branch and should be proposed upstream rather than kept as a private fork.

**Host-local daemons are per-hypervisor, never per-VNI.** Any responder
that must run as a daemon on the hypervisor runs as **one process per host
serving all VNIs**, not one per network — a process/namespace per VNI is
the dnsmasq-per-network anti-pattern OVN and Neutron's distributed-DHCP
both abandoned, and it does not scale to hundreds of realms. DHCP and DNS
are served by a **single merged responder** (`exordos-evpn-nf`) for
exactly this reason; proxy/netboot, if it ever needs a daemon, folds into
the same one. The per-VNI context comes from the
packet (the VLAN tag / in_port), not from a dedicated process. A per-VNI
daemon is allowed only as a genuine last resort. Records are held in
memory and refreshed on change, not re-read per request.

### Compilation

Network functions apply to ports of `ovs_evpn` networks only;
`flat_bridge` ports keep the legacy path (central ISC DHCP, no NF) until
infrastructure networks migrate to OVS (decision 13).

**Fan-in and return traffic.** Per-packet context travels in registers
on `br-int`, not in the table: the pipeline stamps the VNI/VRF and the
port/delivery context into registers, the tables match packet fields only
and pass the registers through, and what passes the filter goes through
the patch into `evpn`, where `evpn_connector` does the Type-5/VXLAN
delivery by its own registers. Return traffic is a **conntrack-assisted**
traversal in the opposite direction on `br-int` — stateful rules use
`ct(zone=VNI)`, since realm CIDRs overlap on one host and identity is
always the pair (VRF, port), never a bare IP. Conntrack reverses NAT and
gates stateful-firewall replies, but **not** arbitrary transformations:
an NF that rewrites non-NAT fields (MSS clamp, DSCP, mirror, encap)
declares a reverse action the compiler emits on the reply direction —
see "Performance, offload and debuggability". Punt NFs answer on
`br-int`: UDP one-shot responders (`dhcp`, `dns`) receive the punt and
reply straight to the requesting port; TCP services (`proxy`) terminate
in a per-VRF veth+namespace (the OVN-metadata pattern), where an
ordinary socket and the namespace's conntrack handle the return path.

The `ovs_evpn` builder compiles what serves the port into the per-host
`evpn_node` payload; the agent renders it into **`br-int` flows** it owns
(`splitter` ⇒ the conntrack pipeline, `dhcp`/`dns`/`proxy` ⇒ intercept
flows to the local responders) plus the `evpn_connector` client config
for the port's VNI (the patch-port side). `fip` ⇒ local NAT, which rides
the `evpn` side. A function serving many ports compiles once per host,
delivered as its own `evpn_nf` slice rather than copied into every
port.

### Performance, offload and debuggability

The common variable behind performance, hardware offload and debug is
**pipeline depth** — how many tables/`resubmit`s and `ct()` recirculations
a packet traverses. The one-OVS-table-per-NF layout below is a
*compilation choice*, not a requirement, and the compiler is expected to
trade it off explicitly.

**Throughput is nearly free; connection-setup rate is not.** OVS
collapses a multi-table traversal into a single datapath **megaflow** on
the first packet of a flow; subsequent packets bypass the tables
entirely, so NF-graph depth costs almost nothing for bulk throughput
(~Mpps/core on the fast path). What it costs:

- **connection-setup rate** — every new flow re-traverses all tables in
  the slow path (an upcall), so more NFs lower the new-connections/second
  ceiling. This hits short-flow workloads (provisioning, HTTP storms),
  not bulk transfer.
- **megaflow cache pressure** — every field an NF matches (5-tuple,
  ct-state, registers) widens the megaflow mask; fine-grained splitters
  and stateful NFs produce narrow megaflows and thrash the cache. The
  cost driver is *how many fields* the graph matches, not the table count
  itself.

**SmartNIC offload is bounded by depth.** Modern NICs (ConnectX ASAP²,
BlueField, Intel IPU) offload OVS megaflows into the eSwitch. VXLAN
encap/decap, match+output+field-rewrite, and even **conntrack** (offload
of established connections) all offload on current hardware — so the
EVPN/VXLAN forwarding core and shallow NF graphs (a firewall + a forward)
offload well. The limit is **recirculation**: each `ct()` and deep
`resubmit` chain is a hardware pipeline pass, and NICs support only a
bounded number, so a **deep NF graph falls back to software** for those
flows. `multipath`-ECMP and `learn` offload poorly (prefer hardware
groups); punt-to-host NFs (`dhcp`/`dns`/`proxy`) run on the CPU by
design. Implication: the compiler must keep the datapath **shallow** —
flatten a port's reachable graph toward a minimal match→action set, or
budget depth to the target NIC's recirculation limit — while keeping the
logical graph separate. Tables for humans, flatness for silicon.

**Debuggability is a real strength, with two caveats.** `ovs-appctl
ofproto/trace` walks a synthetic packet table-by-table showing every
match, `resubmit` and verdict; with one table per NF the trace reads like
the NF graph ("entered secgroup, matched drop") — this is the
`exordos network trace` roadmap tool, and per-table counters are the
per-NF stats. Caveats: (1) if the compiler flattens for offload the trace
no longer maps 1:1 to the graph, so every emitted flow carries a
`cookie` = source NF uuid to stay traceable in either form; (2) offload
removes host visibility — an offloaded flow is invisible to `tcpdump` and
shows only the would-be software path in a trace, so debug tooling must
also read `dpctl/dump-flows type=offloaded` and NIC counters.

**Conntrack is scoped, not universal.** "ct for all return traffic" is
wrong on four counts, and the design scopes ct to where it is native and
offloadable — NAT reverse and stateful-firewall replies — and nothing
else:

- ct reverses **NAT tuples only**. Non-NAT transforms (MSS clamp, DSCP,
  mirror, encap change) are not undone by ct; the classic case is a VIP
  ingress MSS clamp whose reply (SYN-ACK) must be re-clamped by an
  explicit `ct.direction=reply` flow. Such NFs declare a reverse action;
  return is not "ct restores and forwards".
- same-VRF symmetric routed `/32` traffic needs **no ct** — adding it is
  pure overhead plus conntrack-table pressure (the hardware ct table is
  finite, ~1M entries, with aging/eviction).
- **ct state is per host, which collides with DVR.** If the forward path
  creates ct state on host A and the return arrives on host B (distributed
  egress/ingress), B has no state and the return breaks — exactly the
  asymmetry that made decision 7 choose routed-VIP-without-NAT. ct-based
  return is only valid when forward and reply traverse the same host (DVR
  egress guarantees this; anycast-VIP ingress + DVR egress does not).
- ct **zones** must be consistent per (VRF) across every stateful NF and
  NAT, and an inter-VRF routing NF needs an explicit zone rule at the
  boundary, or replies land in the wrong VRF's conntrack.

**Validation plan (when the compiler lands).** Three numbers turn the
above from theory into budgets, measured on the spike and later on real
NIC hardware: (a) connection-setup-rate degradation as NF count grows;
(b) megaflow count under diverse traffic; (c) recirculations consumed by
a typical graph and the depth at which offload falls back to software.

### Facts and status model

The UA framework already separates `capabilities` (orchestrator-managed,
compared for reapply) from `facts` (gathered from the DP independently,
synced upward on change, never applied) — the graph uses both, plus
three hard rules learned from the mail element's reapply loop.

Target-resource granularity produced by the `ovs_evpn` builder:

- **`evpn_host`** — per host: gobgpd sessions, VRF/VNI tables; changes
  rarely.
- **`evpn_nf`** — per (host × NF), uuid `uuid5(nf, node)`: the compiled
  slice of a shared NF (rule table, address sets). Editing an NF
  re-hashes only these slices on affected hosts; ports are untouched.
  The slice carries what the function answers with
  (resolvers, the zone's suffix, the netboot filename, the proxy's
  allowlist) and lands beside that VNI's guest records, in the namespace's
  own directory. The slices are refreshed on the loop that already runs,
  for every host with a guest on the subnet — nothing about a port changes
  when a function is edited, and nothing should.
- **`evpn_port`** — per port, scheduled to its host's agent: ClientEdge
  (mac/ip/VNI), entry-table reference, DHCP record, anti-spoof list. It
  names the functions serving it (`{kind, nf}`) and no longer carries
  their configuration.

Hash discipline (hard rules for the `evpn_*` drivers):

1. Target value contains only CP-computed fields; the driver's actual
   value echoes exactly those fields — the hash converges by
   construction, reapply loops are impossible.
2. Anything host-derived stays out of the managed fields: apply
   diagnostics, applied table ids etc. go into non-target fields of the
   actual (they move `full_hash`/upward sync, never `hash`/reapply);
   `ofport` never leaves the host at all (decision 9).
3. No write-backs of DP data into target models; facts land in their own
   storage only.

Counters and statuses are a design, not a surface. **None of it is
built**: nothing samples an `nf_stats` fact, no builder aggregates a
status and nothing writes a reason, so `nf.status`/`status_reason` are
not on the API — `port.status` (the reconcile state) is the only status
the model carries. The shape to build against, when something needs it:
counters as a fact kind per (host × NF) sampled with throttling, the last
sample kept and aggregated across hosts on read; `nf.status` as the
aggregate of its `evpn_nf` actuals; `port.status` as the aggregate of its
`evpn_port` plus the slices of the functions serving it **on its own host
only**, so a function broken on host B degrades only host B's ports; and
compile-time errors (no capable agent on the port's node, a broken
reference) never reaching agents at all, the builder setting `ERROR` plus
a `status_reason` on the object whose input caused it.

### DNS: private names and external publication

**Private names need no new storage.** Two tiers, both served host-locally
by the `dns` NF from data compiled into its per-host slices:

- **Auto-names, derived at compile time** from CP facts: an automatic
  zone per network — `<node-name>.<network-name>.<suffix>` (suffix is an
  installation parameter, default `.internal`) resolving to the node's
  port addresses in that network, and reverse PTR from the same facts. Several nodes sharing a name
  yield multiple A records (round robin). A port without an attached
  node has no name. Nothing is stored: port/claim changes recompile the
  slice.
- **Custom names** live in the existing DNS store (`/v1/dns/` Domains and
  Records). The `dns` NF config lists which domains it serves
  (`zones`, default: the installation's private domain); their records
  are compiled into the same host-local slices, so private resolution
  never depends on a central resolver at runtime. Everything else goes
  to the upstream forwarders.

**External publication reuses machinery that already exists**: every
installation runs an authoritative PowerDNS over its own
`dns_domains`/`dns_records` tables, and the `dns_sync` service pushes
domains marked `sync_to_ecosystem` upward into the parent's DNS API with
realm credentials — recursion by flattening, no delegation needed at
current scale. The deltas:

- Record kind **`A_claim {claim}`** — a reference to a `net_addresses`
  row instead of a literal IP: content is rendered on save, re-rendered
  when the address changes; releasing one with live records is a 409.
  References stay local — `dns_sync` pushes rendered literals upward,
  so no cross-level references exist.
- **`access` kind on Domain** (same pattern and mixin as networks).
  Anti-spoofing rule: in a shared domain a foreign project may create
  only `A_claim` records pointing at its own claims — never arbitrary
  content. Name collisions are first-come; quotas later.
- **DNS is not a failover mechanism**: records are not health-gated;
  availability is routing's job (anycast withdraws in seconds, DNS TTL
  caches cannot).

### IAM

Policies follow the existing `network.*` scheme: `network.network`,
`network.subnet`, `network.port`, `network.nf` (+ `network.network.share` for
publishing). Cross-project visibility of `public` networks is the only
place the controllers deviate from plain project scoping.

### Multi-tenant query mechanics

- `access` stays a JSONB kind, but the table adds a **stored generated
  column** `access_kind` (`access->>'kind'`) with a partial index
  (`WHERE access_kind = 'public'`). A dedicated read-model exposes it, so
  listing visible networks is a plain
  `OR(project_id = ctx, access_kind = 'public')` — restalchemy
  `filters.OR` over two indexed clauses; the write model never touches
  the column.
- A `SharedProjectControllerMixin` overrides `filter()`/`get()` to build
  that OR instead of the forced `project_id` equality; create/update/
  delete keep strict project scoping.
- Validating an address against a foreign subnet is two PK lookups
  (subnet → network → access check) on every port write — no caching
  needed.
- `port.addresses` is inline in the API but **normalized in storage** as
  rows of `net_addresses` (see "The address ledger"): IPAM
  exclusivity becomes a DB constraint, `?network=/?subnet=` port filters
  become joins instead of JSONB containment scans, and
  per-`(subnet, project)` counts give shared-pool quotas for free.
- `access: projects` (roadmap) lands as a grant join table, not a JSONB
  list — grants change independently of the network and want audit.

## Increment 1 scope

Two checkpoints with a re-evaluation between them.

### Checkpoint 1 — data-plane spike + control-plane glue

Stand: the core VM plus one or two new hypervisor VMs on the host machine.

1. Manual data-plane spike (no CP): OVS + gobgpd + `evpn_connector` on the
   hypervisors, RR with LLGR on the core. Validate: VM↔VM within a VRF,
   VRF↔VRF isolation, per-node SNAT egress, **LLGR fail-static** (kill the
   RR — connectivity survives), MTU budget end-to-end.
2. Control plane: `ovs_evpn` driver, `evpn_node`/`bgp_rr` capabilities,
   host-local DHCP responder, VNI/RT allocator, MTU field, ofport facts;
   the API resources above: networks (`driver`/`access` kinds) + subnets,
   thin ports with `addresses`, the `splitter`/`dhcp` functions (`fip`
   if time permits); `proxy` lands with checkpoint 2, which needs it for
   realm-node netboot.
3. Packaging: SDN part in the core element manifest + CLI role enablement.

### Checkpoint 2 — two isolated realms

The ecosystem realm builder creates a per-realm `ovs_evpn` network.
End-to-end: two realms on shared hardware, mutually unreachable, both fully
functional (realm-node VMs netboot through the `proxy` NF inside their
VRF — no flat NIC on VMs, egress to the internet working).

## Roadmap (post increment 1)

- LBaaS ingress on an anycast service address (replaces `ipsv4`-based
  DNS records). This is what `vip` was meant to be the primitive for, and
  what it would have to come back with: an address several ports answer
  for is only useful once something health-checks them.
- Per-network "stable egress IP" (`egress: stable`) via an edge node.
- Second route reflector.
- Infrastructure networks on OVS.
- Direct VM image write by the hypervisor agent (optimization over
  `proxy`-served netboot).
- NF kinds: `mirror`, `ratelimit`, `lb`, `snat`; cloud-init metadata in
  `proxy`.
- Inter-network routing, and whatever explicit composition it needs.
- `access: projects` grants and per-project quotas in shared networks
  and shared DNS domains.
- DNS subzone delegation to realms (instead of flattening `dns_sync`)
  if the realm tree outgrows it.
- `border`/`lb` converge into edge-role NF kinds; `exordos network
  trace` (ofproto/trace over the compiled graph, the counters' twin).

## Non-goals (recorded)

- **IPv6**: v4-only for now. The `addresses` list and the NF model are
  family-neutral, so dual stack later is additive, not breaking.
- **Live migration of VMs**: the `/32` route would move, but conntrack
  state (`fip` NAT, stateful `splitter`, DVR SNAT) would not; out of
  scope until compute needs it.
- **Overlay encryption**: VXLAN rides the underlay in cleartext;
  IPsec/WireGuard between VTEPs is a possible future layer, not part of
  this design.
- **Hairpin to a floating address**: a guest cannot reach its own (or its
  neighbour's) floating address from inside the overlay. Measured rather
  than assumed — from a realm node, `SYN`s to its own `fip` leave on the
  wire and nothing ever comes back, while the same connection to another
  realm's `fip` completes. The `fip` NAT lives in the VRF's egress
  namespace: the packet is `DNAT`ed to the guest and, still carrying the
  guest as its source, `SNAT`ed to the floating address by the same rule
  that gives the guest its outbound identity — so what would arrive back
  at the port is a `SYN` in the *reply* direction of the connection
  conntrack has just recorded, which is invalid, and is dropped. Making
  it work means `SNAT`ing the hairpinned packet to something else (the
  gateway), which hides the client from the receiving guest and hands it
  a source that the receiver's own group does not admit — a second grant,
  to a platform address, on every port. AWS and GCP refuse the same case
  for the same reason. Inside the overlay a guest is reached by its
  address, and the internal zone gives it a name.

## Shapes that were left alone on purpose

Two pieces of the CP driver read as accidents and are not:

- the `evpn_subnet` marker and the two-loop delay (a subnet on the first
  pass, its ports on the second) are load-bearing for the shared
  `NetworkService` reconcile contract — delete-tracking and the
  actual-vs-target diff — so reworking them would touch the flat driver's
  path too;
- `_ensure_rr()` runs from `list_subnets()` because driver instances are
  cached by spec and the contract has no per-loop hook.

## Open questions

- Whether the parent ever needs ingress into a realm's core API (would
  pull ingress work forward).
- Caps on rules and functions per project — compilation must not be a
  DoS vector.
- LB backend addressing: add a `port`-reference endpoint kind next to the
  `host` string kind, so editing `port.addresses` cannot silently break
  pools.
- DB migration for existing installations: the flat network gains
  `driver`/`access` kinds, `Port.ipv4/subnet` become `addresses` rows;
  existing flat ports keep the legacy path.
