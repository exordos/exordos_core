# SDN networking resources

A Network isolates private traffic; its Subnets provide address ranges. A Port
is one NIC attached to a Node. Address resources reserve IPs or bind floating
IPs to Ports. [Quickstart](private-network.md) · [Dataplane](dataplane.md).

## Resources

| Resource | Main fields |
| --- | --- |
| Network | `driver_spec`, `access`; read-only `driver_state`, `is_default` |
| Subnet | `network`, immutable `cidr`, `ip_range`, `dhcp`, `routers`, `dns_servers`, pool flags |
| Port | `node`, `index`, placement, `target_ipv4`, `target_mac`, service/security switches, `owner_node`; allocated `ipv4`, `mac`, `status` |
| Address | `kind`, placement, optional `address`, `port`, `owner_port`; allocation/publication status |
| SecurityGroup | Name and description; read-only `rules` |
| SecurityGroupRule | `security_group`, `direction`, `protocol`, optional `port`, `remote_ip` or `remote_group_id` |
| PortSecurityGroup | Immutable `port`, `security_group` binding |
| NetworkFunction | Owned DHCP/DNS service overrides in `config`; installation-owned proxy |
| ProxyService | HTTP service exposed inside a Network; [service guide](paas-networking-guide.md) |

Collections are under `/v1/network/`: `networks`, `subnets`, `ports`,
`port_security_groups`, `nfs`, and
`catalog/{addresses,security_groups,security_group_rules,proxy_services}`.
Manifest resource types use the same hierarchy below `$core.network`.

Explicit placement takes exactly one of `network` or `subnet`. A Network selector
must resolve to one accessible eligible Subnet; ambiguity returns 409. A Port
without either selector uses the project's default placement policy.
Address creation requires `kind`: `reservation` holds an IP without a Port;
`floating` optionally binds one Port. One floating Address per Port is supported.
An Address's subnet, value and kind cannot change after allocation.

For Subnet `routers` and `dns_servers`, `null` selects automatic defaults:
with DHCP and more than two CIDR addresses, the first host is the default gateway
and DNS server; otherwise both lists are empty. An explicit `[]` disables that
option; an explicit list is used even with DHCP disabled. POST omission means
`null`; PUT omission preserves the stored value, and explicit `null` restores
automatic defaults. Responses return the stored lists and omit `null` fields.

## Node creation and attachment

Node and NodeSet use `default_network` to request one primary Port:

| Input | Result |
| --- | --- |
| Omitted | Automatic placement, without a FIP |
| `{}` | No Port, except inherited flat placement when compatibility selects it |
| `{network: UUID}` or `{subnet: UUID}` | Port in the selected network/subnet |
| `{auto_floating_ip: true}` | Default SDN Port and automatically allocated FIP |
| `{network: UUID, auto_floating_ip: true, floating_network: UUID}` | Explicit private network and floating pool network |

`floating_network` requires `auto_floating_ip=true`. Requesting a FIP without an
explicit private selector always selects SDN, even when the project has flat
Ports. `target_ipv4` and `port_security` are also accepted; a NodeSet template
cannot pin an IP. Creation of Node, default network/subnet, Port and FIP is atomic.

The project default Network has read-only `is_default=true` and is created lazily
with a `10.128.0.0/20` DHCP Subnet. Its name is not used for selection. An IAM
Project row is not required.

`[network] legacy_flat_default=True` lets automatic placement inherit the single
eligible flat Subnet used by existing project Ports. Boot subnets and address-only
pools are excluded; multiple eligible subnets return 409. With no eligible flat
Subnet, placement uses SDN. New installation configurations set this option to
`False`; upgrades retain the existing configuration. Explicit selectors override
this heuristic. Disabling it leaves existing attachments intact.

Node stores `default_network` as an ordinary template. Updating it changes the
stored request without creating, moving or deleting Ports/FIPs; only Node insert
creates them. NodeSet applies its template to new replicas and preserves existing
attachments. Replica networking is managed through NodeSet; an ordinary Node can
have additional standalone Ports.

Network selectors and creation flags reflect the stored template. Read Node's
current primary allocation from `default_network.ipv4`, `.port` and `.floating_ip`.
These read-only values are derived from Port/Address; query the referenced Port
for its actual Network/Subnet after changing a template. NodeSet exposes `nodes[uuid].ipv4` and `.floating_ip`. Floating IPs
are strings, omitted when unbound. These projections describe allocation and
binding; Port/Address status reports dataplane application. List all Node Ports
with `GET /v1/network/ports/?node=NODE_UUID`.

A detached Port has `node=null`, `index=null`. Attachment without an index chooses
the lowest free slot; index 0 is primary for boot/image operations. An attached
index is immutable. Detachment preserves IP/MAC and does not renumber other Ports.
Live and persistent libvirt hotplug are supported. Removing the last Port does
not create a replacement; an unbooted VM needs a primary Port before flashing.

## Floating pools and permissions

An operator prepares a Subnet with `floating_pool=true`, `placeable=false`.
It may contain private or public IPv4 addresses. `shared_pool=true` grants other
projects allocation from that Subnet; Network `access=public` grants visibility
and allocation across the Network. Neither flag creates routing on the uplink.

Without `floating_network`, allocation selects an accessible
`default_floating_pool`, or the only accessible floating pool when none is marked.
No pool returns 400; ambiguity returns 409. Each owner project may mark one
default. Setting pool flags requires `network.subnet.floating_pool`.

Automatic primary Ports require only Node/NodeSet creation permission. Standalone
Ports require `network.port.create`; FIPs require `network.address.create`.
Selecting a specific IP in another project's shared/public pool additionally
requires `network.address.explicit_address`. Network sharing requires
`network.network.share`. These operator permissions are not granted to project
owners by default. Private Port placement and Node/Port/SG/FIP bindings stay
within one project.

## Ownership and deletion

`Port.owner_node` makes the Port follow its Node's lifecycle. Automatic Ports
receive it; independent Ports start unowned. PUT can release ownership with
`null` or adopt an attached Port with `owner_node=Port.node` and Node update
permission. Detachment clears ownership.

`Address.owner_port` does the same for automatically allocated FIPs. Unbinding
clears ownership. PUT may release it without unbinding or adopt a bound FIP with
Port update permission. Ownership changes on replica resources go through
NodeSet. Both owner fields are read-only on independent resource creation.

Node deletion removes owned Ports and their SG bindings; independent Ports detach
and survive. Port deletion removes owned FIPs. Independent FIPs survive Node
deletion; direct Port deletion is blocked while independent FIPs/bindings remain.
Networks and SGs survive Node deletion.

Moving a Port/FIP uses explicit detach/unbind followed by attach/bind. Released
IP/MAC allocations can be reused without a withdrawal report from the former
host. Target removal still triggers cleanup when the host reconciles. If an
offline host returns with stale forwarding state, it can answer for the
former allocation until reconciliation cleans it up. Moving a Port or FIP does
not preserve conntrack sessions.

Address statuses are `UNBOUND`, `IN_PROGRESS`, `ACTIVE`, `ERROR`; ACTIVE requires
the assigned host's report for the current target.

## Security

Without SG bindings, a Port permits traffic unless `enforce_security_group=true`.
That flag defaults to `false`; `true` denies both directions until rules allow
traffic. Bound groups union allow rules; unmatched traffic is denied, including
when a bound SG is empty. SG enforcement is supported on overlay Ports.
`port_security=true` independently enables MAC/IP anti-spoofing.

Rules support `any`, `tcp`, `udp`, `icmp` and a single TCP/UDP port.
`remote_ip` accepts IPv4/CIDR. `remote_group_id` is ingress-only, can reference
another SG in the same project or itself, and matches only member Ports in the
protected Port's Network. It is mutually exclusive with `remote_ip`.
See [remote security groups](security-group-remote-access.md) for limits.

## Updates, manifests and upgrade

Omitted top-level fields preserve current values. Explicit dictionaries and lists
replace their values entirely; `{}` clears NF user overrides. Network
`driver_spec` must include its driver. Read-only projections cannot be used as SQL
filters or sort keys. NF defaults are computed during compilation; configuration
stores user overrides only.

[The example manifest](../../exordos/manifests/examples/sdn-networking.yaml)
shows a VM with automatic FIP and a separately managed Port/SG. Adjust its pool,
image and source CIDR for the installation. Each manifest owns its declared
resources; references do not transfer lifecycle ownership. EM compute and network
resources use the service project. DB-agent project filters supply omitted project
IDs and scope lookups and listings; declarations must use the same project.

Migration `0010-network-sdn-9e41c7.py` follows the master schema and creates the
final SDN tables directly. Existing flat Ports retain UUIDs, IPs, MACs and
attachments; missing `source` is filled from the Subnet name. Existing attached
Ports become Node-owned and receive stable indexes. Empty stored NodeSet network
templates become absent templates. Apply migrations before starting updated
Core, and use matching Core/SDK builds on the control plane and hypervisors.

## Metadata transport

The local guest proxy authenticates callers by the Port MAC/IP intercept and
forwards Boot API requests to Core on TCP 11015. Both requests and responses
are encrypted with ChaCha20-Poly1305 using the serving hypervisor's existing
agent key. Core binds the request to that host, its assigned guest, the method,
path, headers and body; expired and replayed requests are rejected. The proxy
never falls back to plaintext. Installed agents keep their existing encrypted
Orch/Status protocol and keys.

The legacy Seed still uses HTTP on the trusted Boot network before receiving
a key. The legacy listener binds the Boot IP on TCP 11013 and accepts that
interface only. Core maps a static Port IP or an active DHCP lease MAC to the
calling node and scopes sensitive resources to it. Reading a node key is
disabled; key refresh requires the caller's own server-authorized network
installation. This hop requires Boot L2 isolation: source IP/lease mapping is
not cryptographic authentication against another Boot-network participant.

Deploy matching Core/SDK builds together and regenerate the SDN drop-in so the
legacy listener retains its Boot IP while `proxy_endpoint` names the encrypted
listener. For realms with extra hypervisors, Border must forward TCP 11015
instead of TCP 11013. Existing node keys are not rotated automatically.

Encrypted requests are limited to 2 MiB and encrypted response envelopes to
32 MiB (roughly 24 MiB of payload). Oversized responses return encrypted 413.
