# Letting a group reach a private network

For the canonical Port/Address/SG API, use [SDN networking resources](sdn-api-contract.md).
The inline Port security/FIP examples below describe the earlier prototype.

A security group today says what a port accepts by address: its default is
"everything out, and in only from my own subnet" (a `splitter` NF owned by
the subnet). The question this note answers is what comes after that — how a
rule should say *"from those guests"* rather than *"from that prefix"* — and
what each answer costs on the data plane.

## What the other clouds do

They split into two families, and the split is not about API taste. It is
about whether the fabric already carries the *sender's identity*.

### Family A — the group is a set of addresses

**OpenStack Neutron, `remote_group_id`.** A rule's source is another
security group; the platform keeps the member list and expands it.

* *iptables/ipset driver*: one ipset per (group, ethertype), rules match
  `-m set --match-set`. Membership change rewrites the set.
* *OVS firewall driver*: a **conjunctive match**. A `conj_id` is allocated
  per (remote_group, security_group, direction, ethertype, priority offset);
  dimension 1 flows match `nw_src=<member>` once per member, dimension 2
  match the port and the rest of the rule. Both must hit. Without it the
  cost is O(n·m) flows for n members and m ports; with it, O(n+m).
* *OVN*: originally `Address_Set` (a named set of IPs referenced in an ACL
  match), now **Port_Group** — ACLs hang off a group of logical ports, which
  is what let OVN apply conjunctive matches cleanly and made it much faster.
* Neutron later added **address groups** (`remote_address_group_id`): a
  named set of CIDRs. The ergonomics of a group without the membership
  tracking.

**VMware NSX** is the same class: grouping objects with dynamic membership,
resolved to addresses by the manager and pushed to every host that has a
rule referencing them.

The cost is not the flow count — conjunctive match solves that. The cost is
that **a rule stops being local**. Every host referencing the group must
learn every member, and a VM booting, dying or changing address is a write
on all of them. That is the part that is ugly on the data plane, and it is
inherent to the family, not to the implementation.

### Family B — the group is an identity the fabric carries

**AWS.** A rule's source can be another security group: "any interface that
has this SG attached", private addresses only, within a VPC or across
peering/Transit Gateway. Enforcement lives in the virtual-network stack of
the host, where an ENI's group membership is an attribute of the flow
lookup — the sender is *known*, so referencing is a lookup rather than a set
to distribute. Addresses changing underneath a group means nothing.

**GCP.** No remote-group at all: a rule's source is a set of ranges, a
**network tag**, or a **service account**. The last two are instance
identity resolved by the control plane, with the same effect — and service
accounts tie the firewall to IAM, which is a nicer story than a free-text
tag.

**Azure.** NSG rules take ranges, **service tags** (platform-maintained
prefix sets) or an **Application Security Group** — a name that NICs join,
resolved by the platform, enforced per-NIC.

The two families differ in one thing: A distributes the membership, B
distributes the *identity*. B needs somewhere to put that identity.

## Where that leaves us

Our data plane is per-guest: a guest sits in its own access VLAN on
`br-int`, behind its own patch pair into the EVPN bridge, with its
`ct`-based ingress/egress tables keyed by its own cookie. Across hosts,
frames travel VXLAN with the VNI naming the network.

That layout makes both families reachable, in this order:

1. **Address groups first** (family A, no membership tracking). A named set
   of CIDRs a rule can name instead of one prefix. It buys the ergonomics
   people actually ask for — "the office, the bastions, the CI" — at the
   price of exactly what we already pay for a CIDR rule. Nothing new on the
   host at all.

2. **Referencing by expansion** (family A proper), if we want AWS-shaped
   semantics without touching the fabric. Two things keep it honest:
   * compile with **conjunctive match** (`conj_id`) so a group of n members
     across m ports stays O(n+m) flows, not O(n·m) — OVS supports it and we
     are already on OpenFlow 1.3, so `conjunction` is available;
   * ship the membership as **its own resource per host**, the way a
     function's settings already travel (`evpn_nf` = uuid5(nf, node)). A
     member joining then moves one small resource and re-hashes no port —
     the same discipline that keeps an edited resolver from touching a
     guest's data path. Without this, group churn rewrites every referencing
     port on every host, which is the failure mode worth avoiding.

3. **Carry the identity** (family B) — **the chosen direction**, and the
   fabric can carry it, but not the way the obvious reading suggests.

   VXLAN has the place for it: **VXLAN-GBP**, a 16-bit Group Policy ID in the
   tunnel header, which OVS exposes as `tun_gbp_id`. What settles the design
   is our two-bridge layout: security groups live on `br-int`, the tunnel
   lives on the `evpn` bridge that evpn_connector owns, and the two are joined
   by a **patch pair**. Measured on real OVS
   (`gcl_sdk/.../sdn_fabric/test_gbp_carrier.py`):

   * `tun_gbp_id` **does not cross a patch port, in either direction**. A mark
     set on `br-int` is gone before the fabric's bridge sees the packet; a
     mark arriving off the wire is matchable on the `evpn` bridge and gone one
     patch later. Confirmed twice — by `ofproto/trace`, and by a marked ping
     across the two-host fabric arriving with zero hits on the far `br-int`.
   * `pkt_mark` — the kernel skb mark — **does** survive the hop.

   So the carrier between our bridge and the fabric's is `pkt_mark`, and the
   contract with evpn_connector is two field copies at the tunnel boundary,
   where its own flows already are:

   * egress: `move:NXM_NX_PKT_MARK[0..15] -> NXM_NX_TUN_GBP_ID[]` before
     output to `vxlan_out`;
   * ingress: the reverse, before output to a guest's patch.

   Everything else is ours: allocating a group id, putting it on the port, and
   compiling `set_field:<gid>->pkt_mark` on egress plus `pkt_mark=<gid>`
   matches in the guest's ingress table. **Guests of one host never touch the
   tunnel** — the connector routes between their patches — so their mark rides
   `pkt_mark` end to end and needs no translation at all: one carrier covers
   both paths.

   Three costs to book, none of them hidden:

   * evpn_connector must be changed (it owns the `evpn` bridge and rewrites
     its tables wholesale, so these rules cannot be added behind its back);
   * OVS refuses GBP and non-GBP VXLAN **on the same UDP port**, so turning it
     on is a fabric-wide change to `vxlan_out`, not a per-network toggle. What
     it is *not* is a window of breakage: measured on two hosts, rolling one
     back to a plain tunnel neither drops traffic nor loses the identity — the
     option governs what a port encodes, and the bits ride in reserved space a
     plain receiver reads anyway
     (`sdn_fabric/test_gbp_carrier.py::test_a_fabric_halfway_through_the_gbp_change_keeps_working`);
   * `pkt_mark` is the host's skb mark, shared with nftables `meta mark` and
     `ip rule fwmark`. Group ids must fit the low 16 bits and the range has to
     be reserved against the host's other users of the mark — Border's NAT
     rules first.

**Built**: go to 3 directly. It removes the whole
class of problems the other two carry on the data plane — no member lists to
distribute, no flow churn when a VM boots or dies, and a rule that stays
local to the host enforcing it.

## How it is put together

* An **identity group** is a name for a set of workloads and one bit of the
  mark the fabric carries. `network identity-groups add -n web -p <project>
  -N <network>`; the bit is allocated by the platform and is not an option,
  because one chosen by hand could collide and hand another group's access
  away. Sixteen bits means sixteen groups per network — each subnet's
  default group takes one — and the bits are per network because a packet
  never crosses from one to another. Past that the answer is a 400 (below).
* **A rule may only name a group of the caller's own project.** The bits are
  allocated per network, so a rule naming somebody else's group would compile
  to a number that on the port's own network belongs to a different group
  entirely — the caller would be granted something other than what they
  asked for. Refused at the API, like every other cross-project reference.
* A **port joins as many as it needs**: the option repeats, and the mark it
  stamps is the union of their bits, so a rule naming one admits it whatever
  else it is. Its subnet's group is included unless the port opts out with
  `--no-subnet-group`.
* A **rule admits one**, in the syntax rules already had:
  `network sg rule-add <group> ingress:tcp:5432@web`. After the `@` an
  address stays an address and anything else names a group.
* On the hypervisor the port stamps its number into `pkt_mark` on everything
  the guest sends, and an admitting rule matches `pkt_mark`. Between
  hypervisors evpn_connector copies it to and from VXLAN-GBP's 16 bits at
  the tunnel — the only place a tunnel field is reachable, since a patch
  port clears it.
* **The default moved with it.** A subnet gets a group, every port joins,
  and the default rule names that group instead of the subnet's CIDR: the
  one rule that applies to every guest was the last place still deciding who
  is "us" by an address a neighbour can simply choose.

**Ingress only, on purpose.** What a packet carries is its *sender's*
identity, so "from that group" is answerable and "to that group" is not. An
egress rule naming a group is refused by the API and compiles to nothing on
the host, rather than quietly granting everything.

**Unidentified is refused.** A port with no identity stamps nothing, so no
group rule can match it — which is the behaviour to have while a hypervisor
has not caught up.

Proven on two hosts over a real tunnel (`sdn_fabric/test_gbp_carrier.py`)
and on real VMs on a live stand: a member of the admitted group connects, a
member of another group times out on the same port of the same target, and
moving that guest into the admitted group — one command on its own port,
the receiver untouched — lets it in.

**What OVN does, and why we still differ.** OVN splits the same question in
two: an ACL says *whose* rule it is by port-group membership
(`outport == @pg`), which is local and free and lets a port be in any number
of groups; and it says *who may reach them* with an address set generated
from that port group (`ip4.src == $pg_ip4`), which is the member list,
distributed by the southbound database and compiled into conjunctive
matches so the flow count stays O(n+m). GCP is the same shape from further
away — up to 64 tags per instance, and a propagation delay of seconds when
tags change, which is what distributing membership looks like from outside.

We carry the identity instead, so our remote side costs nothing and our
ceiling is bits rather than distribution.

## Past sixteen: refused

A seventeenth group on a network is refused with a 400 that says the
network has no identity bit left, and so is a subnet on such a network,
because its default group would need that bit: a subnet whose guests could
not be given a default group would compile to deny-all between them. The
subnet check is a check and not a reservation — the default group is seeded
by the network service's next pass — so a group created in between can
still take the last bit; that subnet is then left without a default group
(fail closed) and says so in the log. Deleting a group frees its bit.

A fallback that carried a group past sixteen by its members' addresses (an
address set joined to the rules by an OpenFlow conjunction) was built and
removed before release. It was the O(n+m) generality nobody asked for, it
recognised members by source address and so was only as strong as
`port_security`, and its installation-wide join numbers were a second
allocator with its own reuse hazard. If sixteen groups a network ever stops
being enough, the place to look is a wider carrier, not a second mechanism.

Left open: an installation that already seeded an address-based default
keeps it until that generated group is deleted, because a seeded function
is never rewritten; and the fabric-wide GBP switch on `vxlan_out` remains
the part to schedule deliberately, since OVS will not mix GBP and non-GBP
tunnels on one UDP port.

## What we would *not* do

Keep synthesising the member list into each port's own rules. That is the
O(n·m) shape, it re-hashes every port on every membership change, and it
makes a guest's data path depend on a fact about some other guest — the
thing every one of the designs above was built to avoid.
