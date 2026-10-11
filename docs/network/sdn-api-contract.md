# SDN networking resources

[Entity inventory and dataplane diagrams](dataplane.md).

This contract requires compatible Core and SDK versions. An older host agent does
not implement interface withdrawal acknowledgements or multiple NICs.
Flat networking remains available when `Node.networking` is omitted.

## Ownership and references

| Resource | Writable relationship | Read-only views |
| --- | --- | --- |
| Node | `networking` on creation only | `ports` |
| Port | nullable `node`; automatic or explicit `index` | `ipv4`, `mask`, `mac`, `network`, SG/IG lists |
| Address | immutable `kind`; nullable `port` for floating addresses | effective network, allocation, origin, publication, status |
| PortSecurityGroup | immutable `port`, `security_group` pair | — |
| PortIdentityGroup | immutable `port`, `identity_group` pair | — |
| SecurityGroupRule | `security_group`, direction, protocol, optional port/remote | — |
| SecurityGroup | name, description | `rules` |
| IdentityGroup | network on creation | identity bit; automatic group is protected |

Network and Subnet have no reverse `ports` field. SG and IG likewise do
not carry writable or computed membership lists.

Port `dhcp`, `dns`, and `network_identity` are the only service and automatic
identity switches. Group membership uses PortSecurityGroup and PortIdentityGroup
bindings exclusively. `Port.config` and `interfaces[].config` accept only the
`simple` kind's `public` address request; group lists and switches inside
`config` are rejected. Existing manifests must use canonical bindings or
`networking` for group declarations. Saved port settings are migrated;
NodeSet group templates become `networking` templates. Nodes with group
requests that have not produced bindings must finish provisioning or have
their declarations converted before upgrading.

Addresses bound to a Port are listed through the Address collection with a
`port` filter. Node creation returns its `ports`; each Port's canonical
bindings and addresses can be queried through their collections.

Port and floating Address creation accepts exactly one of `subnet` and
`network`. The latter selects one accessible eligible subnet; ambiguity
returns 409 with candidate UUIDs. `floating_pool=true` declares an external
pool independently of `shared_pool` and Network `access`. An external pool
may use RFC1918 addresses. Pools are address-only (`placeable=false`);
creating/changing the flag requires `network.subnet.floating_pool`, a separate
operator permission not granted to project owners. Private overlay networks
can also host approved external pools. A shared pool/public network permits allocation;
explicit address selection in a foreign pool also requires
`network.address.explicit_address`. Bindings and a FIP's private port remain
within the same project. Manifest installation has global operator authority;
explicit project IDs are operator declarations. Omitted project IDs retain
the EM agent's project. The older `foreign_subnet` permission applies to
legacy `interfaces` declarations, not canonical cross-project private ports.

Address `kind` is required: `reservation` reserves an IP without a port;
`floating` can bind one Port. One floating address per Port is supported.
Converting kinds or moving an allocation to a different subnet is rejected.
Consuming a reservation as a Port's private address is not a defined API.

## Node creation and attachment

```json
{
  "name": "vm", "cores": 1, "ram": 1024,
  "disk_spec": {"kind": "root_disk", "image": "ubuntu_24.04"},
  "networking": [
    {"port": {"network": "PRIVATE_NETWORK_UUID"},
     "security_groups": ["SG_UUID"],
     "floating_ip": {"network": "EXTERNAL_NETWORK_UUID"}}
  ]
}
```

`port` and `floating_ip` also accept existing detached/unbound UUIDs.
Creation is one transaction, including the Node, Ports, bindings and FIPs.
`networking=[]` disables automatic ports. Omission preserves flat behavior.
`networking=null` and mixing with legacy interfaces are rejected. Subsequent
changes address canonical resources directly.

Detached Port: `node=null`, `index=null`. Attachment with an omitted index
atomically chooses the lowest free nonnegative index. An explicit attached
null is invalid. An attached index cannot change; detaching does not renumber
other ports. Index 0 is the primary interface for boot/image operations.
A running VM can lose its last NIC without being destroyed; an unbooted VM
needs a primary NIC before it can be flashed. Multiple NICs and hotplug use
live and persistent libvirt configuration.

Node deletion detaches and preserves SDN ports, including generated ports.
Independent SG/IG/FIP dependencies prevent their referenced resource from
being deleted (409). Move a Port/FIP by explicit detach/unbind, then attach/bind.
If a previous publication exists, reuse waits for the previous host's current
withdrawal acknowledgement. Offline hosts retain IP and MAC holds.

Each Node creation request without a UUID creates a new resource. Clients
can supply the resource UUID to identify their creation request.

NodeSet networking is a template for new replicas: only create objects with
automatic IP/MAC/FIP allocation are accepted. Updating the template preserves
existing replicas and their independent bindings. IDs remain stable while the
replica/resource exists. Existing UUIDs or fixed addresses in templates are
rejected by both schema and runtime validation.

## Security

No SG binding permits ingress and egress. Bound groups combine allow rules;
unmatched traffic is denied. A bound empty SG denies everything. Deleting
the last binding restores unrestricted SG filtering. Port anti-spoofing is
separate and defaults to enabled.

There is no implicit/default SG. A deterministic protected automatic IG
covers all opted-in ports of one Network, including different subnets.
`network_identity=false` opts out. A rule's `remote_group` refers to an IG,
not an SG; it is ingress-only and mutually exclusive with `remote_ip`.
Protocols: `any`, `tcp`, `udp`, `icmp`; a nullable single port number is valid
only for TCP/UDP. Port ranges are not part of this version's rule schema.

Initial manifest-declared bindings and SG rules block interface activation
until their rows exist, including loaded dependent elements. Later policy
changes converge independently and fail closed while a bound SG has no rules.

## Partial declarations and state

Omitted fields preserve current values. Schema dictionary fields merge
recursively (`Network.driver_spec`, `NetworkFunction.config`); `{}` is a no-op.
Lists replace atomically; `[]` means empty. `null` clears/inherits only where
the field allows it. Generated/default state is not written into declarations.
Read-only projections use Restalchemy custom properties and cannot be SQL
filters or sort keys (400).

Desired and effective state are separate: Network `driver_spec` vs read-only
`driver_state` (including the effective `mtu`); Subnet route/DNS overrides vs effective views;
NF user `config` vs generated/effective config. Explicit MTU null inherits
the configured underlay minus VXLAN overhead without reallocating VNI/RT.

DNS and proxy intercepts claim the metadata IP and the subnet's actual default
gateway. Traffic to another guest on DNS, Orch/Status or boot API ports follows
the regular network and security policy, including a nested realm's control
plane. Per-port NF descriptors carry these destination selectors so a change
updates the target hash and reapplies the flows. DHCP discovery remains exempt.

FIP statuses: `UNBOUND`, `IN_PROGRESS`, `ACTIVE`, `ERROR`, `DELETING`.
Control-plane attachment is not proof of host application. ACTIVE and release
require a report from the assigned agent's Node, with matching current hash
and a timestamp newer than the publication target. A delete can remain pending
while its host is offline; its address cannot be reallocated meanwhile.

## Manifests

[Complete example](../../exordos/manifests/examples/sdn-networking.yaml)
creates a Node, Port, auto-allocated FIP, SG and rules. Adjust external pool,
image and source CIDR for the installation before applying it. It needs prepared
SDN hypervisors and external connectivity; declaring the pool does not prepare
those hosts.

A second manifest can import the exported Port and create its own FIP and SG
binding. Reapplying the base manifest never replaces a list of ports/groups/FIPs.
Deleting the dependent manifest removes its own bindings/resources first.

## Delivery

Core and SDK must be packaged as one tested dependency set. Current
version bounds alone do not imply these features are present. Release the
SDK backend, hotplug and withdrawal support, and finally Core; regenerate
dependency locks against those artifacts. Read-only views use the existing
Restalchemy custom-property support.
Before upgrading live SDN hosts, install the SDK agent update everywhere and
verify the withdrawal capability. Flat hosts retain their existing single-NIC
wire representation. The migration leaves flat driver specifications unchanged.

The agent update must also support NF destination selectors before Core emits
them. An older agent accepts the field but applies its former port-only rules;
upgrading that agent afterward does not itself change an already reported hash.

Inline Node/NodeSet creation checks the corresponding resource permissions:
Port create/update, Address create/update and SG/IG binding create. Explicit IP
selection in foreign pools has the same privileged check through wrappers and
standalone resources. Project owners receive the new rule/binding CRUD rights;
sharing and explicit-address privileges remain separate grants.

## Stored declarations and upgrade

Migration 0006 moves saved Port flags and memberships to canonical fields and
bindings, and normalizes Node/NodeSet declarations in EM and target resources.
Provision pending group bindings before upgrading. Referenced interface configs
and toggles must be expanded before upgrade. Node/Port group lists must use
literal UUIDs during migration. Source manifests must use the
canonical format. Downgrade with managed SDN declarations requires restoring
a database backup; unrepresentable NodeSet templates stop downgrade.

For installations that already applied 0006, migration 0009 performs the same
normalization, including UA actual Node snapshots, adds the stored NodeSet
activation barrier, and drops the unused creation receipts table. All manifest
SecurityGroupRule resources must already be materialized before 0009 runs;
otherwise the migration stops before changing data. This prevents migrated
NodeSet templates from activating before their initial rules exist. Apply Core
migrations explicitly before restarting its services.
