# Private networks: two nodes and a floating IP

A private network is an EVPN/VXLAN overlay (`ovs_evpn`): the core allocates
its VNI, route target and MTU, and every guest of it is wired into the fabric
by its hypervisor's SDN agent. Guests of two private networks never see each
other, even when their subnets overlap.

This page is the whole flow — preparing a hypervisor, then the same result
declared from a manifest or built with the CLI.

## 0. Asking for one at all

None of this is switched on by default, and there is no flag to switch it
off. An installation gets an overlay because its stand spec names a
`private_network`:

```yaml
stand:
  private_network:            # name / cidr / dhcp, all optional
    cidr: 10.100.0.0/24
```

That block is what makes the bootstrap seed the `ovs_evpn` network, write
the `[evpn]` route-reflector drop-in, and offer the evpn capabilities to
the node's agent. A spec that does not mention it gets none of the three —
which is what an installation that has been running on flat networks looks
like when it is upgraded: nothing is created, nothing is written, no
service is restarted.

### Enabling SDN on a running installation

An installation that was bootstrapped without an overlay gets one without a
new bootstrap, a reboot or a different image:

1. Create an `ovs_evpn` network through the API or the CLI
   (`exordos network networks add -n private -p <project uuid>`). The core notices
   it on its next reconciliation pass: it gives its agent the evpn
   capabilities, restarts that agent, derives the route reflector from the
   address the core answers on, and starts `gobgpd`. No `[evpn]` drop-in is
   needed.
2. Run `hypervisors init --sdn` on every hypervisor that is to carry guests
   of the overlay (see [1. The hypervisor](#1-the-hypervisor)). It is
   idempotent, and guests already running on flat networks are not touched.
3. Add a subnet and place nodes on it with `interfaces`
   (`cn add --network`, or a manifest as below).

Guests that already exist stay on their flat network. A node moves onto an
overlay by being recreated with an interface on it.

## 1. The hypervisor

Once per host, and only for hosts that run guests of an overlay:

```bash
sudo "$(command -v exordos)" compute hypervisors init --sdn \
      --sdn-connector <evpn_connector source> \
      [--add | --sdn-project-id "$PROJECT"]
```

`init --sdn` does what `init` does, then installs Open vSwitch, gobgpd,
evpn_connector and the SDN agent, and maps the machine pool this host serves
(`--sdn-pool`, by default the pool `--add` registers or the installation's
only pool) to the agent that wires its guests. It switches the pool to OVS —
that is what makes libvirt plug a guest's tap into `br-int` where the agent
can find it. A re-run installs nothing that is already there, so it is safe
to repeat. Without `--sdn`, `init` does not touch any of it.

Which agent wires the guests follows from the pool:

- a pool run by an agent on this host (`--add`) — that agent gains the evpn
  driver and the pool names it as its hypervisor;
- any other pool — a dedicated SDN agent is registered for it, which needs
  `--sdn-project-id` for the agent's node.

Nothing else is needed on the host: the agent renders the configuration of
gobgpd and evpn_connector, starts the host-local DHCP/DNS/netboot responder,
and builds the per-network egress namespace as guests appear.

A dedicated SDN agent also gets a node to be — that is where its
uuid and its key live. On an installation with no bare-metal inventory the
scheduler has nothing to place it on and marks it `ERROR / No suitable HW
machines found`. That row is an identity, not a workload; the hypervisor
works regardless, and the message means what it says — there is no HW
machine to give it.

## 2. From a manifest

`exordos/manifests/core-sdn-example.yaml.j2` declares a private network, its
subnet, a pool of public addresses and two nodes placed on the network:

```yaml
resources:
  $core.network.networks:
    private_net:
      name: "example-private"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      driver_spec: {driver: "ovs_evpn"}
      access: "private"
    public_pool:
      name: "example-public"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      driver_spec: {driver: "ovs_evpn"}
      access: "public"

  $core.network.subnets:
    private_subnet:
      name: "example-private"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      network: "$core.network.networks.$private_net:uuid"
      cidr: "10.101.0.0/24"
      dhcp: true
      # routers/dns_servers omitted: a DHCP subnet defaults both to its
      # first host address (10.101.0.1 here). Set them to override.
    public_subnet:
      name: "example-public"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      network: "$core.network.networks.$public_pool:uuid"
      cidr: "203.0.113.0/24"
      dhcp: false
      placeable: false

  $core.compute.nodes:
    node_a:
      name: "sdn-example-a"
      cores: 1
      ram: 1024
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      interfaces:
        - kind: "subnet"
          subnet: "$core.network.subnets.$private_subnet:uuid"
      disk_spec: {kind: "root_disk", size: 10, image: "…exordos-base.raw.zst"}
    node_b: { … }
```

Install it, and the platform creates the network, the subnet and both nodes:

```bash
exordos em ee install core-sdn-example.yaml -p "$PROJECT"
exordos em ee update  core-sdn-example.yaml -p "$PROJECT"   # a later version
```

Two things worth knowing:

- a resource references another by the prefix it is declared under
  (`$core.network.networks.$private_net:uuid`), not by the element's name;
- an element's resources live in the element-manager project, so a
  project-scoped listing does not show them. Publishing a network
  (`access: public`) is what lets another project see it and allocate in it;
- a DHCP subnet that omits `routers`/`dns_servers` defaults both to its first
  host address (`.1`) — the address the hypervisor's egress namespace takes
  and answers DHCP/DNS on — and that address is held out of the guest pool, so
  the first guest starts at `.2`;
- the gateway is the **default** route's `via`, wherever in the list it is
  written, and an overlay subnet is held to having exactly one of those and
  to every `via` being an address of the subnet. The host puts that address
  on a leg with the subnet's own prefix length, so a next hop outside the
  subnet is a leg in a network of its own and a guest with no way out.

`interfaces` is everything a node says about networking, and one entry means
one NIC — here, the one on the overlay. Its address and MAC come from the
subnet unless the entry asks for an address. The node's own `ipv4` is read
back from `default_network`, which is the primary leg (index 0) promoted
onto the node and is read-only. See [Node interfaces](node-interfaces.md)
for the full shape, including attaching a floating address or a security
group in the same declaration.

That last part has a consequence worth stating: a node whose only network is
an overlay has no route to the control plane, so its in-guest agent never
registers and the node stays `IN_PROGRESS` — the guest itself is up and
reachable inside its network, but `cn add --wait` would wait for ever. The
path meant to carry that traffic is the host-local proxy on
`169.254.169.254`, which forwards only what the installation grants it;
until the installation configures it, treat such nodes as workloads you
reach over their own network.

## 3. The same thing with the CLI

```bash
P=<project uuid>
IMG=<url of the base image>

exordos network networks add -n private -p $P
# -r/-s omitted: the gateway and resolver default to the subnet's .1.
exordos network subnets add -n private -p $P -N private -c 10.102.0.0/24
exordos compute cn add -n node-a -p $P -c 1 -r 1024 -d 10 -i $IMG --network private
exordos compute cn add -n node-b -p $P -c 1 -r 1024 -d 10 -i $IMG --network private
```

Every reference takes a name as well as a uuid. A node placed on an overlay
netboots **through that overlay**: its hypervisor answers its DHCP, hands it
a boot script and proxies the boot API, so the guest is installed over the
network it will live on and never touches the installation's boot network.
Nothing has to be prepared for that beyond `init --sdn` above. The hypervisor
gives an overlay guest a boot ROM able to fetch over HTTP; guests on flat
networks of the same pool keep the pool's ROM.

Use an x86_64 iPXE ROM for q35 guests with more than roughly 2.75 GiB of RAM.
[Affected 32-bit iPXE builds](https://github.com/ipxe/ipxe/issues/1760)
cannot access the virtio device's PCI BAR above 4 GiB and stall before DHCP;
the RAM threshold depends on the firmware. Replace an affected
`/usr/share/qemu/1af41041.rom` with
the x86_64 build from `exordos_seed`; `init` keeps an existing ROM file.

Guests are in their network's internal zone as well: `node-a.internal`
resolves inside the overlay, forward and reverse, under the suffix the
network's `dns` function carries.

Filtering is a group attached to the port the platform created with the node:

```bash
exordos network sg add -n web -p $P \
      -r ingress:icmp -r ingress:tcp:22 -r egress:any
exordos network ports list --filters node=<node uuid>
exordos network ports update <port uuid> -g web
```

A rule reads
`<ingress|egress>:<tcp|udp|icmp|any>[:<port>][@<remote cidr>|@<group>]`.
A port with no group of its own is not unfiltered: it gets its subnet's
default group, which lets it start anything outward and lets in only its own
subnet. Attaching a group replaces that default and denies everything it does
not allow, in both directions. An attached group with no rules denies all
traffic.

## 4. Naming who, instead of where

After the `@` a rule can name an **identity group** instead of an address.
The group is a name for a set of workloads; a port joins one and stamps it on
everything the guest sends, so a rule elsewhere admits the group without
anyone distributing the list of addresses in it — and without rewriting that
rule when a guest is created or destroyed.

```bash
exordos network identity-groups add -n web -p $P -N private
exordos network identity-groups add -n db  -p $P -N private

exordos network ports update <port of node-a> --identity-group web
# A port joins as many as it needs — the option repeats.
exordos network ports update <port of node-b> --identity-group db \
      --identity-group web

exordos network sg add -n admits-web -p $P \
      -r ingress:tcp:5432@web -r egress:any
exordos network ports update <port of the database> -g admits-web
```

Now the database admits the `web` group on 5432 and nothing else. Moving a
guest between groups is a change to *that guest's* port; the rule protecting
the database is not touched, and neither is any other host.

Three things worth knowing before relying on it:

- **Ingress only.** What a packet carries is its *sender's* identity, so a
  rule can ask "from that group" and cannot ask "to that group". A group on
  an egress rule is refused rather than quietly ignored.
- **Unidentified is refused.** A port with no identity stamps nothing, so no
  group rule matches it. That is the safe direction while a hypervisor is
  still catching up, and the reason a mixed fabric drops such traffic instead
  of passing it.
- **The subnet's default is a group too.** "Only from my own subnet" holds by
  membership, so a neighbour cannot claim to be one of you by choosing an
  address.
- **A port carries the set of groups it is in**, one bit each, and its
  subnet's default group is included unless it opts out with
  `--no-subnet-group`. So joining a group *adds* access rather than trading
  the subnet's away, and a workload that is deliberately not one of the
  neighbours says so — it is then reachable only where a rule names a group
  it is actually in. A port that leaves the subnet's group and names none of
  its own is unidentified, which no group rule matches.
- **Sixteen bits, so sixteen groups per network**, and each subnet's default
  group takes one of them. The ceiling is the price of carrying membership
  in the packet instead of distributing it: nothing about a group is ever
  sent to another host, so a guest appearing or leaving costs no flow
  anywhere else. Groups are a network's for the same reason — the same bit
  means a different group on a different network, which is safe because a
  packet never crosses between them. Asking for one too many is refused with
  a message that says which network is full.

Names work as well as uuids everywhere, and `network identity-groups list`
shows what exists. The number a group travels as is the platform's and is not
an option — one chosen by hand could collide with another project's group.

## 5. A floating IP

A floating address is a 1:1 NAT the guest's own hypervisor programs, so the
traffic path stays symmetric with distributed egress. Take an address out of
a public pool and bind it to the port:

```bash
exordos network addresses add -p $P -s example-public -a 203.0.113.20
exordos network ports update <port uuid> --public-address <address uuid>
```

Or let the platform pick one:

```bash
exordos network ports update <port uuid> --floating-from example-public-subnet
```

`--floating-from` names the **subnet** to take an address out of, not the
network holding it: which addresses these are is the whole question, and a
network answers it only when it happens to have one subnet.

And to take it away — the address stays allocated to you, which is the point
of keeping it an object of its own:

```bash
exordos network ports update <port uuid> --no-public
```

A floating address comes only from a **floating pool**: an address-only
subnet (`placeable: false`, `--pool-only`) of a flat network or of one
published with `access: public`. Neither kind of network is one a tenant can
make, and that is the point — the hypervisor answers for a floating address
on its own segment and lets every guest reach it, so a tenant's own overlay
subnet, whose CIDR is whatever its owner typed, is refused as a source for
both `--floating-from` and `--public-address`. A project can allocate from a
pool another project published, or from one marked `--shared-pool`. What is *not* the platform's
job is getting the outside world to route that prefix to the hypervisor: on a
lab that is a static route, in a real installation it is the border's uplink.

That last sentence has teeth. A floating address is what the guest *is*, in
both directions: once bound, everything the guest sends leaves as that
address instead of the installation's generic egress. So a guest that had
working outbound traffic loses it the moment it gets a floating address the
network does not route back — the same guest without one keeps working. Route
the pool to its hypervisors, or expect exactly that.

## 6. Reaching a service of the installation

A guest of an overlay has no route to anything internal — that is what the
overlay is for. The platform's own APIs are the exception already: the boot,
orchestration and status APIs are served by the hypervisor's proxy on the
ports it claims, so an agent inside a guest reaches the core by its ordinary
name and never knows the difference.

Everything **else** the installation runs is not covered by that, and a PaaS
control plane is the case that hurts: its agents live on the very guests that
cannot reach it. A *door* says one service may be reached the same way.

```bash
curl -X POST "$CORE/v1/network/catalog/proxy_services/" \
     -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{
           "name": "paas-cp",
           "network": "<network uuid>",
           "port": 11020,
           "destination": {"kind": "node", "node": "<node uuid>"},
           "domain": "paas.internal"
         }'
```

Guests of that network may now open TCP 11020 to that service — **at the
metadata address**, `169.254.169.254:11020`, or by the `domain`, which is made
to resolve there inside this network exactly the way the platform's own name
is. Give the door a `domain` and point your client at it; that is the path
meant to be used.

What does *not* work is addressing the service by its real address. The
bridge steals the port whatever address the guest aimed at, and the packet is
delivered into the hypervisor's per-network namespace — which holds the
metadata address and the subnet's gateway, and nothing else. A packet
addressed elsewhere has no local address to be accepted on and is dropped.
This is the same bargain the platform's own APIs already make (the core is
reached by its name, which resolves to the metadata address), and a door is
deliberately not a second mechanism for it.

> There is no `exordos network proxy-services` verb yet: doors are ordered
> through the API above. The CLI group is the obvious next step and is not
> built.

Three things worth knowing before you rely on it.

**A door names an object, not an address.** `destination.node` is a node of
yours, and the address is the platform's answer about it — its primary
port's, never the node's own `default_network`, which its owner can write —
so a node that gets rebuilt keeps its door, and a node that goes away takes
it with it.
There is a `{"kind": "address", "address": ...}` form for a service the
platform does not model at all, and it needs a permission of its own
(`network.proxy_service.custom_destination`) because what it names is fetched
by the hypervisor, out of the management network. The same permission covers
opening a door on a network that is not
`private`: the isolation boundary is the network, so a published one holds
other projects' guests, and a door stands where their agents expect the
platform.

**The service is told who is calling, and that is a second factor.** Every
request the proxy relays carries `X-Genesis-Node-UUID`, stamped from the
guest's own record — the address cannot be forged, so this is a fact about
the fabric rather than a claim by the caller, and any copy the caller sent is
dropped before it goes anywhere.

It is not a login. Whatever is behind the door is also reachable directly
from the management network, where anything — a tenant's VM on the flat
network included — can send that header itself. So keep authenticating your
callers exactly as before and use this to *check* them: "this token says node
X, and the fabric agrees it is node X". A service that treats the header as
the authenticator is trusting a claim any neighbour can make.

**The Boot API verifies delegated runtime identity.** On port 11013, the
hypervisor signs the caller UUID, method, path and query, body digest, timestamp
and nonce with its existing agent key. The Boot API verifies that the signing
hypervisor currently owns the caller's machine pool, rejects expired or replayed
requests, and scopes runtime facts to that caller. Direct runtime callers are
identified by their recorded source port; a contradictory identity header is
rejected.

The `guest_machine` kind retains the Seed provisioning contract on the isolated
Boot network, including dynamically leased callers without a recorded port.
This provisioning path does not authenticate callers with the identity header.
The Boot network must therefore remain isolated from tenant networks; it is a
trusted provisioning plane, not a runtime endpoint tenants may access directly.

**The relay is HTTP and only HTTP.** It fetches `http`/`https`, never
`file://`, and it does not follow redirects — a 302 comes back to your guest
to deal with, because following one would be the door quietly becoming a door
to somewhere else. If the service behind it redirects as part of normal
operation, its client has to handle that itself.

Ports 80 and 11011–11013 are the platform's own and cannot be doors: a door
there would answer for every agent on the network.
