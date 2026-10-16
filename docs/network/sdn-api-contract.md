# SDN networking resources

[Entity inventory and dataplane diagrams](dataplane.md).

This contract requires compatible Core and SDK versions. An older host agent does
not implement interface withdrawal acknowledgements or multiple NICs.
New VMs receive a primary port according to the compatibility policy.
Existing flat networking and explicit `interfaces` retain their behavior.

## Ownership and references

| Resource | Writable relationship | Read-only views |
| --- | --- | --- |
| Node | `default_network` on creation | `default_network` |
| NodeSet | `default_network` template for new replicas | `nodes[uuid].ipv4`, `nodes[uuid].floating_ip` |
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
`config` are rejected. Existing manifests must use canonical binding resources.
Saved port settings are migrated. Convert NodeSet group templates to independently
managed bindings before upgrading; unsupported templates stop migration.
Nodes with pending group requests must finish provisioning or have their
declarations converted before upgrading.

Addresses bound to a Port are listed through the Address collection with a
`port` filter. Query a Node's ports with `/v1/network/ports/?node=NODE_UUID`; each Port's canonical
bindings and addresses can be queried through their collections.

Port creation without `subnet` or `network` uses the project's default network.
Explicit Port placement and floating Address creation accept exactly one selector.
`network` selects one accessible eligible subnet; ambiguity
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

Node and NodeSet support the same primary-port creation template:

| Creation input | Result |
| --- | --- |
| `default_network` omitted | Automatic port according to compatibility policy, no FIP |
| `{}` | Flat port if compatibility selects a flat subnet; otherwise no port |
| `{network: UUID}` or `{subnet: UUID}` | Selected network/subnet |
| `{floating_ip: true}` | Default private port and an automatic FIP |
| `{network: UUID, floating_ip: {network: POOL_UUID}}` | Selected network and FIP pool |

Read-only `Network.is_default` identifies the default independently of its name.
Each project has at most one default network. It and its `10.128.0.0/20` DHCP
subnet are created lazily when automatic placement selects SDN.
An IAM Project record is not required. Node, Network, Subnet, Port and FIP
creation is atomic; invalid explicit selectors do not fall back to the default.

Upgraded installations without the new option retain compatibility through:

```ini
[network]
legacy_flat_default = True
```

Without `network`/`subnet`, existing project flat ports determine placement.
A single eligible VM subnet is inherited; multiple eligible flat subnets return
409. NodeSet replicas use their owner's project. Detached ports also count;
boot subnets and address-only pools are excluded. No flat ports, or disabling
the option, selects the project's default SDN network. New configurations set
`legacy_flat_default = False`; upgrades restore persisted configuration unchanged.
Explicit placement overrides the heuristic. An empty `default_network: {}`
retains the automatic flat port when compatibility selects a flat subnet;
otherwise it disables the automatic port. Existing attachments remain
intact when the option is disabled.

If `default_network` requests a FIP without an explicit private `network` or
`subnet`, Node and NodeSet replicas use the project's default SDN network,
even when flat ports exist. This applies to both `floating_ip: true` and an
explicit floating pool. Explicit private placement takes precedence.

Automatic FIPs select an accessible `default_floating_pool` subnet, or the only
accessible `floating_pool` when no default is marked. No pool returns 400;
ambiguity returns 409. Each owner project can mark one default floating pool;
multiple accessible marked defaults require explicit selection. The flag requires
`floating_pool=true`, `placeable=false`, and operator permission
`network.subnet.floating_pool` to set or clear it. FIP creation requires
`network.address.create`. Public pools are not created automatically.

The automatic primary port through `default_network`, including explicit
network/subnet placement, only requires Node/NodeSet creation permission.
Standalone Port creation requires `network.port.create`.

Node applies the request only on creation. GET keeps `default_network.ipv4`
as the primary port's address; PUT cannot change the creation template.
`default_network.floating_ip` is the primary Port's currently bound floating
address as a string, omitted when no FIP is bound. It reflects binding changes
without waiting for dataplane publication. `default_network.port` identifies the
primary Port; Node has no reverse `ports` field. Query the canonical Port API for
all interfaces and their status. IP allocation precedes dataplane application;
Port status reports application. Removing the last port does not replace it.

NodeSet stores the template for new replicas, each with its own Port/FIP.
Changing it preserves existing attachments. Private addresses remain in
`nodes[uuid].ipv4`; `nodes[uuid].floating_ip` is the primary Port's currently bound
floating address as a string, omitted when no FIP is bound. These are allocated
addresses; the output does not assert host application.
Fixed addresses in templates, null, unknown fields, and mixing with
`interfaces` are rejected. The removed `networking` field is rejected.

For example, an explicit private network with an automatically allocated FIP:

```json
{
  "name": "vm", "cores": 1, "ram": 1024,
  "disk_spec": {"kind": "root_disk", "image": "ubuntu_24.04"},
  "default_network": {
    "network": "PRIVATE_NETWORK_UUID",
    "floating_ip": {"network": "EXTERNAL_NETWORK_UUID"}
  }
}
```

The Node, primary Port and FIP are created atomically. Additional NICs, SG/IG
bindings and subsequent changes use the canonical resources directly.

Detached Port: `node=null`, `index=null`. Attachment with an omitted index
atomically chooses the lowest free nonnegative index. An explicit attached
null is invalid. An attached index cannot change; detaching does not renumber
other ports. Index 0 is the primary interface for boot/image operations.
A running VM can lose its last NIC without being destroyed; an unbooted VM
needs a primary NIC before it can be flashed. Multiple NICs and hotplug use
live and persistent libvirt configuration.

A Port's `owner_node` identifies the Node whose deletion also deletes the Port.
Creation wrappers assign ownership to automatic ports, including NodeSet replicas.
Independent ports start without an owner. PUT can release ownership with
`owner_node=null`, or adopt an attached Port with `owner_node=Port.node` and
permission to update that Node. Detaching a Port clears its owner automatically.
A detached Port can attach to another Node after host withdrawal acknowledgement;
attachment alone leaves it independent unless ownership is explicitly assigned.

Automatically allocated FIPs similarly have `owner_port`. Unbinding clears it;
PUT can release ownership without unbinding, or set `owner_port=Address.port`
with permission to update that Port. The new owner must be in the resource's
project. Port/FIP ownership and attachment changes for NodeSet-managed ports
must go through NodeSet. Ownership is read-only during independent resource
creation, and stays in the control plane, outside the host's configuration.

Deleting a Node deletes its currently owned ports and their SG/IG bindings.
Independent ports detach and survive; groups, networks and subnets survive.
Deleting a Port deletes its owned FIP; independent FIPs detach and remain
allocated during Node deletion, or block direct Port deletion (409), as do
independent group bindings. A concurrent release of ownership prevents the
former owner from deleting the released resource.

Published IP/MAC allocations remain held until host withdrawal is acknowledged.
Move an independent Port/FIP by explicit detach/unbind, then attach/bind.
If a previous publication exists, reuse waits for the previous host's current
withdrawal acknowledgement. Offline hosts retain IP and MAC holds.

Each Node creation request without a UUID creates a new resource. Clients
can supply the resource UUID to identify their creation request.

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

Migration 0009 adds `Network.is_default`, `Subnet.default_floating_pool`, an internal
Port flag for inherited flat placement, and uniqueness constraints. It removes
the saved `networking` and inline barrier fields. Simple primary-port templates
and installed declarations become `default_network`; multi-port, adopted-port
or group templates must be converted to independent resources before upgrading.
Old empty stored NodeSet templates become absent
templates, preserving their previous meaning. Existing node attachments remain
intact. Source YAML with `default_network: {}` retains automatic flat placement
while compatibility selects a flat subnet; otherwise it disables the automatic
port. Apply migrations before restarting Core.
