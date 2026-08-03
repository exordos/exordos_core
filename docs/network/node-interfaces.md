# How a node says what network it is on

A node declares its networking with `interfaces`, a list. What came of that
declaration is read back from `ports`, and the primary leg's address is
promoted onto the node as `default_network` so that every element that
already reads a node's address by that name keeps working.

```yaml
$core.compute.nodes:
  web:
    name: "web-1"
    cores: 2
    ram: 2048
    interfaces:
      - kind: "subnet"
        subnet: "$core.network.subnets.$private:uuid"
        config:
          kind: "simple"
          security_groups:
            # Security groups are made through the API, not a manifest.
            - "b3c1f6de-8a4e-4c1b-9d8e-2f7a5c0e1d42"
          public:
            address: "$core.network.catalog.addresses.$fip:uuid"
    disk_spec:
      kind: "root_disk"
      size: 10
      image: "https://repo.exordos.com/exordos-base/1.1.0/exordos-base.raw.zst"
```

That is one command's worth of what used to take two: create the node, then
find the port the platform gave it and update that port to attach the
floating address. A manifest could not express the second step at all.

## What replaced what

`default_network` was one untyped bag playing three parts at once:

| part | keys | who wrote them |
| --- | --- | --- |
| the request | `network`, `target_ipv4` | the caller |
| the result | `subnet`, `port`, `ipv4`, `mask`, `mac` | the control plane |
| the name everything else reads an address by | `ipv4` | — |

The request half is now `interfaces`. The result half stays where it is:
`default_network` is the primary port promoted onto the node, and once a
port exists the control plane overwrites the field with it.

### `default_network` is deprecated, not sealed

For one more release the two input keys are still read, and only when the
node declares no `interfaces`:

| key | still read | replaced by |
| --- | --- | --- |
| `network` | yes, with a warning | `interfaces[].network` (or better, `.subnet`) |
| `target_ipv4` | yes, with a warning | `interfaces[].address` |

It would be tidier to make the field read-only in one step, and that is
wrong for a reason worth writing down: **writing a read-only field is a
400, not a dropped key.** `default_network.target_ipv4` is how the released
placement path is asked for a fixed address, so sealing the field would
break the next reconcile of any installation whose manifest uses it — on
upgrade, with nothing changed on their side.

`interfaces` wins wherever it is set, so a manifest that has moved is
unaffected. One difference in the legacy path is deliberate: a declared leg
is refused when its network has several placeable subnets, but a legacy pin
in that position is reported and resolved the old way, by order. A pin that
has been working on a live installation is not the place to start refusing.

## The three states

`interfaces` is a list, and its absence is not an empty one.

| written | means |
| --- | --- |
| not set | the node said nothing; placed by the legacy rule — one port, on the first placeable non-overlay subnet, or where `default_network` pins it |
| a list | the whole truth about the node's legs |
| `[]` | the node wants no networking at all |

Nova spells the same three `auto`, an explicit list, and `none`, and for
the same reason: "place me somewhere sensible" and "place me nowhere" are
different instructions, and neither is an empty list of specific requests.

Every node that predates `interfaces` is in the first state, which is why
the legacy rule was kept exactly as it was rather than restated: overlays
stay opt-in, boot subnets and address pools are never placement targets,
and an already-placed node is never moved.

## An interface

Two kinds, and they are mutually exclusive by construction rather than by a
rule nobody reads.

`kind: subnet` — build me a leg:

| field | |
| --- | --- |
| `subnet` | where it goes. This is the real answer |
| `network` | sugar for the ordinary case of one placeable subnet on it |
| `address` | an address to ask for, instead of what the pool hands out |
| `index` | device order; **index 0 is the primary interface** |
| `config` | the port's own `simple` kind — groups, `public`, `dhcp`/`dns` |
| `port_security` | anti-spoof, as on the port |

`kind: port` — adopt a leg that already exists:

| field | |
| --- | --- |
| `port` | the port to take |
| `index` | device order |

An adopted port keeps its own configuration. The node names it and nothing
more, and because the node did not create it, the node does not destroy it.

### Why the subnet and not the network

`network` is accepted and resolved when the answer is unambiguous. When the
network has more than one placeable subnet the request is refused with the
candidates listed, rather than resolved by whichever row came back first.

That silent pick is what the old `network` pin did, and it is worth naming
as a defect rather than a preference: subnet iteration order is the
database's, so the same manifest could place nodes on different subnets on
two installations and neither would be wrong.

### Why an index and not a `primary: true` flag

It does two jobs. It says which leg is primary — index 0, by definition —
and it is what the device order inside the guest will be read from, which
was previously whatever order the ports happened to be created in. Left
unset, indexes are handed out in declaration order, so an ordinary
single-legged node writes nothing. What a VM is built with today is the
primary leg alone; see the end of this page.

## What the other clouds do

| | where an interface lives | primary | subnet or network | adopt an existing one |
| --- | --- | --- | --- | --- |
| GCP | embedded in the instance, `networkInterfaces[]` | position, `nic0` | both (a region disambiguates) | no |
| Azure | a separate top-level resource; the VM references it | explicit `primary: bool`, exactly one | subnet | only this way |
| AWS | inline spec **or** a reference, one slot | `DeviceIndex`, 0 is primary | `SubnetId` | `NetworkInterfaceId` instead of `SubnetId` |
| OpenStack Nova | same | list order | `uuid` (a network) | `port` instead of `uuid` |
| Yandex | same | `index`, server-assigned 0,1,2… | `subnetId`, required | no |

Three things were taken from that survey and one was left.

**Taken: the subnet is what an interface names.** Three of the five require
one. GCP accepts a network only because a region makes the subnet unique,
and Nova accepts one at the cost of the ambiguity described above.

**Taken: an integer index rather than a boolean.** Azure needs a boolean
because its NICs are independent resources with no natural order; ours are
a list, and a list has one.

**Taken: inline spec and reference share a slot and exclude each other.**
AWS and Nova both do this, and Nova states the consequence outright —
"requested security groups are not applied to pre-existing ports". It has
to be a rule, because the alternative is two writers on one port with no
way to say whose value is current.

**Taken: promote the primary leg's address to a top-level scalar.** EC2's
`Instance` carries `privateIpAddress`, `subnetId` and `vpcId` alongside the
full `NetworkInterfaceSet`. GCP did not, and the visible result is that
everyone writes `networkInterfaces[0].networkIP` by hand. `default_network`
is that scalar here, and keeping it is why no existing element manifest had
to change.

**Left: launch-time-only semantics.** In all five, the interface list is a
create-time argument and later changes go through separate operations
(`addNetworkInterface`, attach/detach ENI, a separate NIC resource). In
Terraform the equivalent block is `ForceNew`.

We cannot copy that, because a manifest here is continuously reconciled
rather than submitted once. So `interfaces` converges — and the cost of
converging is that it would fight the `/v1/network/ports` API, where an
operator can attach a leg by hand.

That is what `provenance` on a port settles. A port built from a
declaration is `generated` and the compiler owns it: it is created, updated
and removed to match. A port made any other way is `user` and the compiler
only ever reads it — a declaration that does not mention it is not a
request to remove it. Naming it in an interface is how it joins the
declaration without being taken over. The same distinction, with the same
two words, already governs generated network functions.

## What this deliberately does not do

**A port still carries exactly one address.** `interfaces[].address` asks
for the primary address of a leg; secondary addresses, alias ranges and
IPv6 are not modelled. AWS and GCP both have them; we have no caller yet.

**`role` is not a field.** Boot subnets and address pools are still
recognised by `next_server` and `placeable` rather than by a role on the
port. Naming them properly touches the boot provisioning path, which this
change does not.

**A built leg is edited in place only where that is possible.** A leg's
`config` and `port_security` follow the declaration on the existing port. Its
subnet and address are what the leg *is*, so a declaration that moves either
— including two legs swapping indexes — has the port at that index removed
and a new one built, with a new address unless one is pinned. Nothing tries
to carry a port from one index to another.

**A VM comes up with its primary leg, and only that one.** Every declared
leg becomes a port — its address is allocated, and on an overlay its data
plane slice is compiled — but what the pool driver hands the hypervisor is
one interface, the one at index 0. So on a VM the index decides which leg
is primary and, through that, the device the guest sees; it does not yet
order several devices inside one guest, because there is only ever one.
Declaring more than one leg on a VM is therefore a way to reserve
addresses and ports, not a way to give the guest a second NIC.

## Related

- [Private networks](private-network.md) — the user-facing how-to
- [Security groups across a network](security-group-remote-access.md) — what
  `config.security_groups` and `config.identity_groups` mean
- [Realm connectivity SDN](realm-connectivity-sdn.md) — the overlay these
  legs are plugged into
