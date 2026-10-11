# Realm Connectivity SDN (EVPN/VXLAN)

How realm networks are isolated from each other and connected to the world:
what the overlay is made of, why each piece is shaped the way it is, and what
that shape cost. The control plane it describes is the `ovs_evpn` driver in
this repository; the host side is the evpn capability driver in `gcl_sdk`,
which renders the target resources into `evpn_connector` and `gobgpd`
configuration.

For the current entity inventory and topology diagrams, see
[Network dataplane](dataplane.md). API fields and mutation semantics are
maintained in the [SDN API contract](sdn-api-contract.md).

## Context and goals

An ecosystem installation hosts **realms** — nested, fully fledged
`exordos_core` installations running as VMs on the ecosystem's hardware. The
current network model is a single flat L2 segment (`flat_bridge` driver, ISC
DHCP on a Linux bridge) shared by everything, which gives:

- no isolation between realms (shared broadcast domain, colliding private
  CIDRs on shared hardware);
- no managed public ingress/egress for realm services.

Public connectivity of the core node is provided by the **border**: a
`border_agent` universal-agent capability on the core node providing egress
SNAT and `(ip, port)` DNAT forwards via nftables. This document describes the
architecture that adds isolation on top of it and generalizes connectivity.

## Architecture decisions

### 1. Data plane: OVS + gobgp, reusing `evpn_connector`

The data plane reuses the `evpn_connector` project (gobgp over gRPC + Open
vSwitch/VXLAN) **for EVPN forwarding only**. It already implements EVPN
Type-5 routes, VRFs, ECMP, ARP proxying and anycast prefixes with health
checks, and consumes simple per-host JSON client configs — a natural fit
for universal-agent delivery. It is treated as a **third-party
dependency**, kept unmodified: everything above EVPN (the filter,
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

**One process, one namespace per VNI.** The responder — DHCP, the internal
resolver and the netboot/metadata proxy in one daemon — runs once per host.
Each VNI has a namespace on the host (`vni<n>`, shared with that VNI's DVR
egress) holding one internal port of br-int (`nf<n>`) that carries the
well-known metadata address. The daemon runs a thread per VNI that enters
that namespace once with `setns`, so its raw socket and the proxy's listener
belong to the tenant: overlapping guest CIDRs never share a socket, and the
VNI of a frame is implicit. One process rather than one per VNI is for
density on a hypervisor with many tenants.

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
nodes, never a first-class appliance; the border on the core node is
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
  gobgp's gRPC (50051, which gobgpd binds to loopback only: the API
  authenticates nobody) and **VXLAN (4789)** — as a backstop for the
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

A per-realm **"stable egress IP"** is not modelled. It would point the realm
VRF's default route at an edge node, which SNATs into one stable address —
the same `SnatRule` mechanism as the border, at the cost of hairpinning — and
nothing in the API names it, so egress is distributed and nothing else.

### 7. Ingress: a floating address per port, answered on the underlay segment

Ingress into an overlay is a **`fip`**: 1:1 NAT on the guest's own
hypervisor, inside the VNI's egress namespace (DNAT in, SNAT out), so
forward and reply traffic cross the same host and its conntrack — symmetric
with DVR egress (decision 6). The client address is preserved.

The floating address is on no interface. The hypervisor answers for it on
the segment the address belongs to with a **per-address proxy-ARP entry**
(`ip neigh replace proxy <fip> dev <link>` — never the `proxy_arp` sysctl,
which would answer for every routed prefix, and never `ip addr add`, which
would expose the host's own services on it), routes the `/32` into the
namespace, and sends a gratuitous ARP when the address arrives so neighbours
forget the previous host. The fabric carries only `l2vpn-evpn`, so nothing
announces the address to the underlay: this works while the pool's segment
is L2-adjacent to the hypervisors; behind a router the pool has to be routed
to them by the operator.

The pool does not have to be publicly routed. Realm CIDRs overlap on
purpose, so from the parent's side a realm node is told apart by its
floating address, taken from a pool of private addresses of the same L2
segment: the parent's shared LB on the core node (nginx) proxies public
traffic to it.

Public addresses are not a separate resource type: they are ordinary
allocations in a **floating pool** — an address-only subnet
(`placeable: false`) of a flat network or of a network shared to consumers
via `access: public` (see the control-plane API below). A tenant's own
overlay subnet is never one: its CIDR is the tenant's to choose, and a
floating address is answered for on the hypervisor's segment and opened
through the fabric guard to every guest.

The border `Forward` (DNAT) carries non-LB `(ip, port)` forwards. It is not
a way into an overlay: under distributed egress the reply leaves through the
guest's hypervisor and bypasses the edge's NAT state.

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
- **Fail-static in `evpn_connector` is mandatory.** BGP-layer retention
  alone does not hold: measured, the reflected `/32`s left the hypervisors'
  RIBs within ~10 s of the RR going away (LLGR was not negotiated, and an
  admin stop sends a NOTIFICATION that flushes routes without RFC 8538),
  and `evpn_connector`, which reconciles flows from the local RIB, removed
  the flows with them. `[gobgp] fail_static` (default on): while any
  configured peer is not ESTABLISHED, the connector unions the freshly
  computed flow set with the last snapshot taken while all peers were
  healthy, so a lost session cannot delete flows, and deletions still
  propagate while peers are healthy. Measured: a 2-minute RR outage kept
  flows and guest connectivity. Killing the RR and checking that the flows
  survive is one of the fabric tier's cases.

Security: the DVR SNAT leak (decision 6) gives realm VMs a TCP path to the
RR sourced from a legitimate hypervisor uplink address, i.e. a chance to
impersonate a peer and inject Type-5 routes into foreign VRFs. BGP (179)
and gobgp gRPC ports MUST be blocked in the leak; sessions SHOULD use
TCP MD5/AO on top.

**The reflector**: the CP driver maintains a `bgp_rr` target resource for
the RR agent (`[evpn] rr_agent` / `rr_peer_prefixes`, or the core node
derived from the database when unset), and the host driver renders a
reflector config with **passive peering via gobgp dynamic-neighbors** over
the underlay prefixes (RR-client peer group, GR+LLGR) — adding a hypervisor
touches nothing on the RR. The dynamic-neighbor prefix list is itself a peer
filter — one more reason the SNAT leak must not expose port 179.

**One node may hold both fabric roles.** In the single-host
stand the core node is its own reflector *and* a hypervisor, and gobgpd
reads exactly one file — so each role writes a fragment under
`/etc/gobgp.d/<kind>.json` (its global section and its own body) and
`/etc/gobgp.conf` is assembled from every fragment present: one identity
(the reflector's, since `bgp_rr` sorts first) plus both roles' sections.
Removing one role
reassembles what is left and only the last one out stops gobgpd. A host
whose configured RR address is its own is not given a session to itself
(it never comes up; its own gobgpd already holds the RIB its
`evpn_connector` reads), which is what makes the deployment rule
"control node ≠ workload hypervisor" a preference rather than a
constraint.

### 9. Config delivery: universal-agent capabilities, no shared directory

Configuration travels the way the border's does, as universal-agent
capabilities, with no shared directory:

- on every hypervisor agent: **`evpn_host`** (gobgpd session parameters —
  AS, RR addresses — and the installation's published addresses),
  **`evpn_port`** per guest (mac, `/32`, VNI, RTs, DHCP record, security
  rules, identity, floating addresses) and **`evpn_nf`** per host × function
  (the function's config, see "Facts and status model");
- **`bgp_rr`** on the reflector's agent: reflector config (LLGR, passive
  peering).

The new network driver **`ovs_evpn`** (`gcn_network_driver` entry point) is a
pure *computer* of these target resources from `Network`/`Subnet`/`Port`
models (same builder pattern as border/LB); it never touches hosts itself.

**Guest wiring is scheduled to the hypervisor**: a guest VM
plugs into its *hypervisor's* br-int, so `evpn_port`/`evpn_host` target
resources go to the hypervisor node's agent, not to the guest.
`MachinePool.hypervisor_node` (set by
`exordos compute hypervisors init --sdn`) names that node; the
driver resolves `port.node → Machine → pool → hypervisor_node` and defers
emission until the guest's machine is placed on a pool. A pool without
the mapping keeps the legacy behaviour (the node wires itself — the
self-hosted-fabric path).

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
rate limiting and anycast ingress would need.

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

The installation keeps two drivers side by side: realm networks on `ovs_evpn`
(the realm builder creates one network per realm), infrastructure networks on
`flat_bridge`. The target end state is a single OVS data plane; migrating
infrastructure networks to OVS is not part of this design (the boot network stays
semantically flat-on-underlay regardless of bridge technology). Existing
realms are migrated by recreation; live flat→EVPN migration is out of scope.

## Control-plane API

The [SDN API contract](sdn-api-contract.md) describes canonical fields,
bindings, address allocation, placement and update semantics. The complete
[dataplane inventory](dataplane.md) maps these resources to their targets and
runtime components. Keep those references as the source for the current API;
the sections below explain the forwarding design.

### Guest-visible semantics of `ovs_evpn` networks

- The overlay is L3-only. No broadcast or multicast crosses hosts;
  same-network guest traffic is routed using Type-5 routes, and the local
  host answers ARP. Guest VRRP, mDNS and L2 multicast clustering cannot span
  hypervisors. Services needing fan-in use LB.
- DHCP, DNS and allowlisted platform HTTP services run host-locally.
  Core seeds `dhcp` per Subnet and `dns`/`proxy` per Network. Generated
  defaults refresh; DHCP/DNS user overrides remain. Proxy configuration
  is installation-owned and cannot be edited/deleted by a tenant.
- SecurityGroup, SecurityGroupRule and PortSecurityGroup are separate API
  resources. Several bound groups union their allow rules. A Port without
  any SG binding has no SG filtering; an attached empty group denies new
  tenant traffic. Anti-spoofing and platform service intercepts are separate.
- IdentityGroup and PortIdentityGroup express peer identity. The automatic
  network identity is not a default SG and grants no access on its own.
- FIP is an Address with `kind=floating` associated through `port`; it
  compiles into host-local 1:1 NAT. The guest keeps its private address.
  The legacy `simple.public` config remains an allocation request;
  canonical association uses Address resources.
- `splitter` remains a model kind but is not seeded or selected for runtime
  NF slices by the current compiler. Active policy comes from SG bindings.
  There is no NF graph, `vip` Port kind, inter-VRF edge or stable-egress mode.

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
- **`+rel` is admitted beside `+est`.** The errors a permitted conversation
  generates — destination-unreachable, port-unreachable,
  fragmentation-needed — belong to it, and admitting only `+est` drops them.
  Everything looks correct without this until the day something needs to say
  "that did not work": most expensively, a guest whose path MTU drops
  somewhere never hears about it, so its connections complete their
  handshake, carry small packets and then hang for ever on the first large
  one, with nothing in any allow-list looking wrong. Neutron and OVN both
  admit related traffic for the same reason.

#### What revoking a rule reaches

The pipeline admits `ct_state=+est+trk` outright, so a connection opened
while a rule held would go on working after the rule was withdrawn. That is
not what a revocation means, so the rules that leave a port's allow-list are
turned back into conntrack tuples and the datapath flushes those in that
guest's zone: what stopped being permitted, and nothing else. A rule that
stayed keeps its sessions. Neutron's OVS driver does the same; Calico
deliberately keeps such sessions, and this was a choice between them.

`dpctl/flush-conntrack` takes a zone and a partial tuple, so this costs one
call per withdrawn rule and nothing reads the host's conntrack table to find
the entries that matter. What a tuple cannot say is who the far end was — a
rule admitting an identity group admitted it by a mark on the packet, and a
conntrack entry has no such field — so those cut by protocol and port.

Nothing is cut while the allow-list is unchanged, so a reconcile does not
disturb what it finds, and a port's first apply withdraws nothing.

Worth knowing when reading the tests: flushing an entry does not by itself
end a permitted conversation. The next packet makes a new entry and the
pipeline lets it through again, so a guest whose whole zone was flushed
looks fine from the outside while every flow it had is re-evaluated and its
NAT state rebuilt — and on a host that does not accept mid-stream packets,
while its connections drop. Precision here is asserted against the conntrack
table, not against traffic.

### Dataplane ownership: two bridges

`evpn_connector` is a **third-party dependency** and stays one: it
provides EVPN/VXLAN forwarding and nothing else. It owns its bridge
(`evpn`) whole — it rewrites the entire flow table atomically every sync
(`replace-flows`), so **nothing else may write flows on that bridge**.
We therefore do **not** put NF logic into `evpn_connector`; the NF layer
lives on a **separate, agent-owned bridge**, and the two are joined by an
OVS **patch-port** pair. This is the OVN `br-int` + `br-tun` split:

```text
guest tap ─▶ br-int (agent-owned: SG, dhcp/dns/proxy intercepts)
                │
                ▼  patch-port
             evpn  (evpn_connector-owned: EVPN Type-5, VXLAN)  ─▶ underlay
```

- **`evpn` bridge — 100% `evpn_connector`.** Unmodified external
  dependency. Its client config lists the **patch-port** as the local
  port for a VNI; it resolves that ofport and does EVPN as usual.
- **`br-int` — 100% the agent** (the evpn capability driver). Guest
  interfaces plug in here; the agent owns all NF flows and the host-local
  responder daemon (`dhcp`/`dns`/`proxy`), with no `replace-flows`
  conflict since it is the sole writer of this bridge. What passes the
  filter goes through the patch into `evpn`; return traffic comes back
  through the patch.

There is **one patch-port pair per guest** per host (`pi<vlan>`/`pe<vlan>`,
keyed by the guest's own local VLAN): the `evpn_connector` client config
points its `ofport` at that guest's patch, so `evpn_connector` announces
the guest's `/32` and routes between guests over their distinct patches,
and `br-int` runs the functions. It is per **guest**, not per VNI, because
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

Owning `br-int` also **keeps the punt NFs simple**: no pausing of the
forwarder and no flows on `evpn_connector`'s bridge. Per guest, intercept
flows on `br-int` (matched on the guest's `in_port`) rewrite the frame's
destination MAC to the VNI's `nf<n>` port and output it there; the one
per-host responder (`exordos-evpn-nf`, a raw `AF_PACKET` socket per VNI, **no
OpenFlow controller / os-ken dependency**) answers from the thread that
lives in that VNI's namespace (decision 4).

*DHCP* serves **only static `mac→ip`** plus a few options (mask, router,
DNS, MTU, netboot filename) — strictly less than dnsmasq: no lease DB, no
dynamic allocation, no TFTP. Replies are crafted from the guest's
control-plane record and addressed to the guest that asked.

*DNS* is unicast to the gateway, which `evpn_connector`'s catch-all ARP
proxy already answers (`router_mac`). The responder hands out the **gateway
itself as the resolver** (DHCP option 6), and the guest's intercept flows
**punt `udp`/`tcp` port 53 to `nf<n>` before NORMAL forwards it to the EVPN
patch** (the OVN/Neutron distributed-DNS pattern, on the bridge *we* own).
The reply echoes the query's destination as its source. Internal `A`/`PTR`
come from the VNI's guest records (`name` → `ipv4` + reverse); the
`platform_names` resolve to the metadata address; everything else is
relayed to the function's `forwarders`.

*Netboot/metadata proxy.* Unlike DHCP/DNS, the proxy is **stateful
TCP/HTTP**, so it uses the kernel stack: `nf<n>` holds the well-known
cloud-init address (`169.254.169.254`) behind a fixed MAC, and the guest's
intercept flows send HTTP for it — and the platform's and doors' TCP ports,
whatever address the guest aimed at — to that port. The listener lives in
the VNI's namespace, so two realms that reuse a guest IP never share a
socket. It serves netboot artifacts from a local cache and an allowlist of
upstream prefixes; everything else is 404 (never an open relay). Shipped as
`gcl_sdk...drivers.evpn_nf` and `evpn_proxy`.

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

Five cases, with nothing between them:

- a **published subtree** — the local netboot cache, or an artifact prefix
  the installation granted — is static files, identical for every guest,
  and any of them may *read* it. Consequence for configuration: a grant
  narrower than `/` means "a subtree published for guests", so an API must
  never be granted under one;
- an **api** is an upstream that authenticates its own callers — a child
  realm calls its parent's ecosystem with the realm's own tokens, not as
  the node it happens to run on. Judging it here would be judging a
  conversation this proxy is not part of, so it is relayed whole. What
  makes that safe is who may name one: `proxy` is the installation's
  function, closed to callers;
- a **service** — a door, below — is relayed like an `api` and identified
  like a platform grant: its paths are not judged, but a caller this VNI
  has no record of is refused rather than relayed anonymously;
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

### Doors onto services of the installation

The platform's own APIs are reached through the ports the proxy claims by
number. Everything **else** an installation runs had no way to be named —
and a PaaS control plane is exactly that: its agents live on the very
guests that have no route to it. Putting an endpoint inside every tenant's
network to fix that is expensive and wrong; a door is the cheap version of
the same intent.

A door is one row (`/v1/network/catalog/proxy_services/`): a network, a
port, and a **destination object** — a node, or (privileged) a bare
address. The compiler folds a network's rows into the `services` map of its
`proxy` function, and the port is claimed on `br-int` like the platform's
own.

The destination is an *object* rather than a URL, and that is the load-
bearing decision. What a door names is fetched by the **hypervisor** —
from the management network, with the caller's credentials in hand, and
with the answer relayed back into the guest. A URL would make every door a
request that the platform reach somewhere on the caller's behalf, with
nothing between that and the installation's insides but a validator's
opinion about a string. An object moves the question to ground the platform
already stands on: *is this yours?* — the same check every other citation in
this API answers. The address is then looked up rather than accepted, and a
node that goes away takes its door with it instead of leaving one pointed at
whatever inherits the address.

Three refusals follow from the same sentence, and each has a live failure
behind it:

- **a platform port** (80, 11011–11013) may not be a door. It would put a
  caller's upstream where every agent on the network sends its own reports,
  and no guest would have any way to notice;
- **a network that is not private** needs the privileged permission. The
  isolation boundary is the network, so a published one holds guests of
  other projects — and a door stands at the address and ports their agents
  treat as the platform's;
- **a bare address** needs it too, for the reason the object kind exists.
  The escape hatch is for services the platform does not model, and it
  belongs to whoever runs the installation.

**A door has to be the only path.** Everything this is worth rests on it.
The identity the proxy stamps is trustworthy because a guest has no other
route to the upstream — the fabric guard drops its egress into the space the
management network lives in — so it cannot go around the proxy and arrive
unidentified. A second path does not weaken that; it turns identification
from a property of the path into a suggestion, and an agent that skips it
looks exactly like one that cannot.

So a destination has to be somewhere the hypervisor's root namespace reaches
and the guests do not, which is where the platform's own services already
live. Two shapes are refused for breaking it: a **guest of an overlay** (its
private address is not routable from root at all, and making it routable is a
distributed virtual router, not a door — deferred deliberately), and a node
with a **floating address** (publishing one puts an ACCEPT above the guard's
drops for the guests of every network on the host, which is exactly the
second path). `[evpn] service_addresses` is the same hazard said by an
operator rather than by a row: an address listed there is reachable by guests
directly, so listing a door's upstream in it silently undoes the door.

**Where a guest addresses a door.** At the metadata address, or by a name
that resolves there — the door's optional `domain` is added to the network's
`dns` function beside the platform's own names. Not at the service's real
address: the bridge steals the port whatever address the guest aimed at, and
delivers the frame into the VNI's namespace, which holds the metadata address
and the subnet's gateway and nothing else. A packet addressed anywhere else
arrives with no local address to be accepted on.

That is the same bargain the platform's own APIs make — the core is reached
by its name, resolved to the metadata address — and a door is deliberately
not a second mechanism for it. The alternative considered and refused was a
DNAT per door inside the namespace: it would make the real address work, at
the price of a second way for a packet to reach the same listener, with the
rules to keep in sync and no benefit a `domain` does not already give.

Two things the relay itself does, and neither is optional. It fetches
**http/https only** — the standard library's default opener carries a
`FileHandler`, so `file:///etc/shadow` as an upstream is not a malformed
grant but a working one that reads the hypervisor. And it **does not follow
redirects**: a grant is of one upstream, and a 302 is how one upstream
silently becomes another, by an upstream that need not be hostile, only
compromised. The 3xx is relayed to the guest, which is the party entitled to
decide.

**The identity header is a second factor, not a credential.** On every
relayed request the proxy drops any client-supplied `X-Genesis-Node-UUID`
and stamps the caller's real one, taken from the guest record — the source
address is allocated by the control plane and the port's own flows are the
only way a frame carrying it reaches the listener, so who is asking is a
fact of the fabric rather than a claim.

What that does **not** buy: an upstream on the management network can be
addressed directly by anything else on it — a tenant VM on the flat network
included — and a header on a request that never came through the proxy is
not one the proxy made. So a service behind a door authenticates its callers
exactly as it did before; the header tells it whether the token it was given
belongs to the node the fabric says is calling. Treating it as the
authenticator would be building on a claim any neighbour can forge.

Making it a credential means proving the hop, and the cheap-looking proofs
do not hold: the hypervisor's address is not a first-class fact of the
platform (it is parsed out of a libvirt connection URI, or reached through
the pool's node), the endpoint that would list it is admin-scoped, and on a
multi-homed host the address a service sees is whichever one the route to
*it* chose. `NodeEncryptionKey` is a shared secret between the core and the
node rather than a key pair, so a signature a PaaS could verify on its own
is not available either. A signed assertion is the upgrade path; it is a
piece of work, not a flag.

**Isolation on `br-int`.** A flat NORMAL bridge is one L2 domain, so guest
ports (with possibly overlapping CIDRs) are separated by a **per-guest
local VLAN tag** on the guest port and its patch — the OVN internal-VLAN
pattern, one guest per VLAN; the VLAN also names the guest's conntrack
zone. Because every guest owns its patch and VLAN, `EvpnPort.delete_from_dp`
garbage-collects that guest's dataplane on its own delete — the patch pair,
its cookie's flows and the local-VLAN reservation; the VNI's namespace goes
with the VNI's last guest on the host.

The only change we carry in `evpn_connector` is **fail-static** — a
general robustness fix (retain flows while a BGP peer is down) that is
not exordos-specific. It currently lives only on our `evpn_connector`
branch and should be proposed upstream rather than kept as a private fork.

### Compilation

Network functions apply to ports of `ovs_evpn` networks only;
`flat_bridge` ports keep the legacy path (central ISC DHCP, no NF) until
infrastructure networks migrate to OVS (decision 13).

**Return traffic.** What passes the filter on `br-int` goes through the
patch into `evpn`, where `evpn_connector` does the Type-5/VXLAN delivery.
Stateful rules use `ct(zone=<guest vlan>)`, since realm CIDRs overlap on one
host; the conntrack pipeline admits established and related replies.
Conntrack is used where it is native — the filter and the `fip` NAT — and
both sit on the guest's own hypervisor, so forward and reply always cross
the same host's state. Punt NFs answer from the VNI's namespace: UDP
(`dhcp`, `dns`) replies are sent straight to the requesting port, TCP
(`proxy`) is terminated by the namespace's kernel stack.

The `ovs_evpn` builder compiles each port into an `evpn_port` resource
scheduled to its hypervisor's agent; the agent renders it into **`br-int`
flows** it owns (SG rules ⇒ the conntrack pipeline, `dhcp`/`dns`/`proxy`
⇒ intercept flows to the local responder) plus the `evpn_connector` client
config for the port's VNI (the patch-port side), and a `fip` into NAT in the
VNI's egress namespace. A function serving many ports compiles once per
host, delivered as its own `evpn_nf` slice rather than copied into every
port.

### Facts and status model

The UA framework already separates `capabilities` (orchestrator-managed,
compared for reapply) from `facts` (gathered from the DP independently,
synced upward on change, never applied), plus three hard rules learned
from the mail element's reapply loop.

Target-resource granularity produced by the `ovs_evpn` builder:

- **`evpn_host`** — per host: gobgpd session parameters and the
  installation's published addresses; changes rarely.
- **`evpn_nf`** — per (host × NF), uuid `uuid5(nf, node)`: the compiled
  slice of a shared DHCP/DNS/proxy NF. Answer settings re-hash these slices
  on affected hosts. Intercept selectors are part of the port target, so
  a change to ProxyService door ports can also re-hash ports.
  The slice carries what the function answers with
  (resolvers, the zone's suffix, the netboot filename, the proxy's
  allowlist) and lands beside that VNI's guest records, in the namespace's
  own directory. The slices are refreshed on the loop that already runs,
  for every host with a guest on the subnet.
- **`evpn_port`** — per port, scheduled to its host's agent: mac/ip/VNI/RTs,
  DHCP record, security rules, identity, floating addresses. It names the
  service kinds and intercept selectors, including proxy door ports.
- **`bgp_rr`** — on each reflector node: ASN and dynamic-peer prefixes;
  the agent renders the route-reflector role in gobgpd.

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

The API exposes Port reconciliation status. It does not expose per-NF
traffic counters, an aggregated `nf.status` or `status_reason`. Agent target
statuses describe their individual applications; they are not a network-wide
service health monitor.

### DNS: private names and external publication

**Private names need no new storage.** The `dns` NF answers, host-locally,
an automatic zone compiled from CP facts: `<name>.<zone_suffix>` (the
port's name, else its node's; suffix default `internal`) resolving to the
port's address, plus the reverse `PTR`. A port without a name, or with the
`dns` function detached, is not published, and neither is a name that is
not a single DNS label (letters, digits and inner hyphens, at most 63):
a dotted name would claim names below the suffix. Nothing is stored: port changes
recompile the records. Everything else goes to the upstream forwarders.

**External publication** uses machinery that already exists: every
installation runs an authoritative PowerDNS over its own
`dns_domains`/`dns_records` tables, and the `dns_sync` service pushes
domains marked `sync_to_ecosystem` upward into the parent's DNS API with
realm credentials. Not built: custom records from `/v1/dns/` in the
internal zone, a record kind referencing a `net_addresses` row instead of a
literal IP, and `access` on domains.

### IAM

Policies follow the existing `network.*` scheme: `network.network`,
`network.subnet`, `network.port`, `network.nf` (+ `network.network.share` for
publishing). Cross-project visibility of `public` networks is the only
place the controllers deviate from plain project scoping.

The project owner role is given the plain `network.*` permissions and none
of the gates — `network.network.share`, `network.address.explicit_address`,
`compute.node.foreign_subnet`, `compute.node_set.foreign_subnet`,
`network.proxy_service.custom_destination`. Every project creator holds that
role, and a binding's project does not narrow what a role grants, so a gate
bound to it would gate nobody. The admin's wildcard covers them; anyone else
is granted one on purpose.

Fields the compiler writes are read-only over the API: a network's
`driver_spec` after creation, a subnet's and an identity group's `network`
after creation, an address's `origin`, `owner_port` and `association`, a
network function's `kind`.

### Multi-tenant query mechanics

- `access` is a plain column (`private` | `public`) with a partial index
  (`WHERE access = 'public'`), so listing visible networks is a plain
  `OR(project_id = ctx, access = 'public')`; subnets of public networks are
  listed the same way. Create/update/delete keep strict project scoping.
- Validating an address against a foreign subnet is two PK lookups
  (subnet → network → access check) on every port write — no caching
  needed.
- An explicit grant list on `access` would belong in a join table rather
  than a list column — grants change independently of the network and want
  audit.

## Non-goals

- **IPv6**: the platform is v4-only. The `addresses` list and the NF model
  are family-neutral, so dual stack is additive rather than breaking.
- **Live migration of VMs**: the `/32` route would move, but conntrack
  state (`fip` NAT, stateful SG filtering, DVR SNAT) would not; out of
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
- the per-loop maintenance — the reflector's resource, the agent's overlay
  capabilities, the status actualization — runs from `list_subnets()`
  because a driver instance is cached per network and the contract has no
  per-loop hook.
