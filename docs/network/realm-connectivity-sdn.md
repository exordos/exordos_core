# SDN deployment

Core compiles network resources; hypervisor agents apply OVS/EVPN, host-local
services and NAT. See [dataplane](dataplane.md) for packet paths and
[quickstart](private-network.md) for commands.

## Host and fabric requirements

Prepare each hypervisor with `compute hypervisors init --sdn`. Its pool's
`hypervisor_node` identifies the agent that wires guest Ports. Libvirt creates
taps on `br-int`; the agent resolves their OpenFlow ports locally.

The SDK owns `br-int` policy, service intercepts and local patches.
`evpn_connector` owns the `evpn` bridge and EVPN forwarding. Do not install
independent flow managers on those bridges.

The fabric uses EVPN Type-5 routes, VXLAN UDP 4789 and VXLAN GBP policy marks.
Participating hypervisors need underlay reachability to each other and TCP 179
to their route reflector. Gobgp gRPC is host-local. The underlay must exclude
untrusted VXLAN/BGP senders; network isolation does not encrypt tenant traffic.
Reserve the low 16 packet-mark bits for SG identities.

The route reflector is outside the guest packet path. During BGP loss the
connector retains its last synchronized forwarding state; new or moved guests
still need route convergence.

## Configuration

Core derives the route reflector from its serving address when configuration
does not specify one. Relevant network-service settings:

```ini
[evpn]
as_number = 65001
vni_range_start = 10000
vni_range_end = 19999
underlay_mtu = 1500
dns_zone_suffix = internal
```

`rr_addresses`, `rr_agent`, and `rr_peer_prefixes` configure an explicit reflector
and its agent/dynamic peers. An externally managed reflector needs no Core-owned
RR agent. Keep host and RR ASN/peer configuration consistent.

A Network can override `driver_spec.mtu`; otherwise its MTU is underlay MTU minus
50 bytes. Each nested realm adds another encapsulation layer. DHCP publishes
the effective MTU to guests; the physical path must carry it.

`dns_forwarders` supplies installation resolver defaults. `image_origins` allows
extra image origins on the management network through the boot proxy.
`service_addresses` allows explicitly selected address/port destinations through
the egress guard. Keep these operator grants narrow. Tenant-created DNS overrides
accept public IPv4 resolvers only.

### Flat access and infrastructure protection

The underlay transports VXLAN between hypervisors and BGP to the reflector.
Management networks carry infrastructure administration. The main flat subnet
carries ordinary VM and platform-service traffic. These roles are independent
of the `flat_bridge` driver and of the project's default placement network.
A connected route on a hypervisor does not identify an underlay network.

Operators can allow private networks to reach selected flat IPv4 subnets:

```ini
[evpn]
flat_prefixes = 10.66.0.0/24
protected_prefixes = 10.65.0.0/24,10.66.0.11/32,198.51.100.0/24
```

`protected_prefixes` identifies underlay and management destinations, including
remote networks. It takes precedence over `flat_prefixes`. The hypervisor's own
addresses, transfer networks, link-local/reserved destinations and fabric ports
remain protected from flat grants. Existing `service_addresses` and floating-IP
publications remain explicit exceptions; do not publish infrastructure addresses
as unrestricted endpoints.

Both lists default to empty, preserving the conservative egress policy. Configure
the main flat subnet explicitly; the bootstrap flat Network also contains a
boot/PXE subnet, which requires a separate access decision. No tenant API field
or project default-network change can modify these installation-wide lists.

Flat access uses the existing SNAT path and permits guest-initiated connections
and replies. It does not create peering between private networks or allow new
connections from flat into a private subnet. Established connections retain the
existing stateful behavior when a grant is withdrawn. Flat services must
authenticate callers; SNAT does not preserve the tenant's source IP.

Choose flat destination ranges outside tenant subnets. If they overlap, the
tenant's connected route can take precedence over the external destination;
flat grants do not translate or disambiguate overlapping addresses.

Flat VM ports use the bridge path without EVPN security groups. Isolate transport
and management from untrusted flat neighbors with VLANs or infrastructure-side
ACLs. If infrastructure and services share an address, use explicit service-port
exceptions or separate hosts with appropriate listener/firewall policy.

Install the matching SDK on hypervisors before upgrading Core. The egress-policy
migration marks existing `evpn_host` targets for reconciliation. The agent stores
the policy locally and repairs missing rules; each flat-policy chain replacement
is atomic. The policy changes neither fabric source selection nor routing.

## Egress and floating IPs

Each participating host provides a gateway for its local guests in a per-VNI
namespace. Distributed SNAT uses the hypervisor's uplink without a Border VM.
The fabric guard blocks management/underlay destinations except explicitly
allowed installation services. Platform HTTP traffic uses the host-local proxy.

For FIPs, reserve a reachable address range and create an address-only floating
pool. On a directly attached external segment, the serving hypervisor answers
ARP for each active FIP and applies symmetric NAT. Routed installations must
provide the equivalent return path. A FIP's outbound traffic uses that address,
so an unreachable pool breaks return traffic even when generic SNAT worked.
The FIP is not assigned to a guest or host interface.

Allocation reuse does not wait for the previous host's withdrawal report.
A returning host can retain stale forwarding until it reconciles. Check
Port/Address status and the assigned agent before investigating upstream routing.
Moving a Port or FIP does not preserve conntrack sessions.

## Boot and services

Overlay guests obtain DHCP and netboot through their serving hypervisor.
Use a suitable x86_64 iPXE ROM for q35 guests; physical provisioning keeps its
flat boot network. The shared `exordos-evpn-nf` daemon serves per-VNI DHCP/DNS/HTTP
records. Changing DNS/DHCP overrides updates NF slices without restarting the
fabric. Proxy upstreams come from installation configuration and ProxyService.

Nested realms run independent Core/agent/RR stacks. Their parent sees an ordinary
VM with its outer Ports/FIP; each child manages its own VNI and address space.

## Upgrade

Apply the SDN migration after the existing master migrations and use matching
Core/SDK builds on Core and hypervisors. Existing flat bridge specifications,
Port allocations and attachments are preserved. Automatic placement compatibility
is controlled by `[network] legacy_flat_default`; see the
[API contract](sdn-api-contract.md#node-creation-and-attachment).
