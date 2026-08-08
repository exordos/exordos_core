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

Adding an overlay later through the API is enough too: the bootstrap that
follows sees the `ovs_evpn` network in the installation and configures the
reflector for it. Otherwise the network would exist while the fabric it
needs was never set up, and that reads as a broken overlay rather than as a
bootstrap that was never asked.

## 1. The hypervisor

Once per host, and only for hosts that run guests of an overlay:

```bash
exordos compute hypervisors init                  # libvirt, storage, OVS + br-int
sudo -E exordos compute hypervisors install-sdn \
      --connector <evpn_connector source>         # gobgpd, connector, agent, units
exordos compute hypervisors register-agent \
      --name hypervisor-sdn -p "$PROJECT" --pool auto --write-config
```

`install-sdn` skips whatever is already installed, so it is safe to re-run.
`register-agent --pool auto` maps the installation's machine pool to this
host's agent and switches the pool to OVS — that is what makes libvirt plug a
guest's tap into `br-int` where the agent can find it.

Nothing else is needed on the host: the agent renders the configuration of
gobgpd and evpn_connector, starts the host-local DHCP/DNS/netboot responder,
and builds the per-network egress namespace as guests appear.

`register-agent` also creates a node for the agent to be — that is where its
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

  $core.compute.nodes:
    node_a:
      name: "sdn-example-a"
      cores: 1
      ram: 1024
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      default_network:
        network: "$core.network.networks.$private_net:uuid"
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

`default_network` is everything a node says about networking: its port,
address and MAC come from the subnet, and the guest gets exactly one NIC —
the one on the overlay.

That last part has a consequence worth stating: a node whose only network is
an overlay has no route to the control plane, so its in-guest agent never
registers and the node stays `IN_PROGRESS` — the guest itself is up and
reachable inside its network, but `cn add --wait` would wait for ever. The
path meant to carry that traffic is the host-local proxy on
`169.254.169.254`, which forwards only what `[evpn] proxy_forwards` allows;
until the installation configures it, treat such nodes as workloads you
reach over their own network.

## 3. The same thing with the CLI

```bash
P=<project uuid>
IMG=<url of the base image>

exordos network networks add -n private -p $P --driver ovs_evpn
# -r/-s omitted: the gateway and resolver default to the subnet's .1.
exordos network subnets add -n private -p $P -N private -c 10.102.0.0/24
exordos compute cn add -n node-a -p $P -c 1 -r 1024 -d 10 -i $IMG --network private
exordos compute cn add -n node-b -p $P -c 1 -r 1024 -d 10 -i $IMG --network private
```

Every reference takes a name as well as a uuid. A node placed on an overlay
netboots **through that overlay**: its hypervisor answers its DHCP, hands it
a boot script and proxies the boot API, so the guest is installed over the
network it will live on and never touches the installation's boot network.
Nothing has to be prepared for that beyond `install-sdn`/`register-agent`
above, which is also what gives the guest a boot ROM able to fetch over HTTP.

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
not allow, in both directions.

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

The pool is an ordinary network with `access: public`, so a project can
allocate from a pool another project published. What is *not* the platform's
job is getting the outside world to route that prefix to the hypervisor: on a
lab that is a static route, in a real installation it is the border's uplink.

That last sentence has teeth. A floating address is what the guest *is*, in
both directions: once bound, everything the guest sends leaves as that
address instead of the installation's generic egress. So a guest that had
working outbound traffic loses it the moment it gets a floating address the
network does not route back — the same guest without one keeps working. Route
the pool to its hypervisors, or expect exactly that.
