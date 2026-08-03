---
icon: lucide/network
title: Networking for a managed service
---

How a PaaS element should place its nodes, so that a tenant can reach them from
their own private network and nobody else can.

Written from taking `dbaas` through the question. Where something is not built
yet it says so — a guide that quietly describes plans as facts is worse than no
guide.

## Decide the control path first, and make it a pull

Everything else follows from this one choice.

If your control plane *dials* its data-plane nodes, those nodes cannot live on
a tenant's private network: you would have to reach into an isolated segment
from outside, and every later problem is a consequence of trying.

If instead each node runs an agent that talks *outward* — the shape `dbaas`
uses, where the capability driver keeps `psycopg` and Patroni on `127.0.0.1`
and the agent initiates everything — the node can live anywhere its agent can
reach you from. That is what makes a private data plane possible at all.

Concretely: no inbound management port, no "the CP polls the instance", no
credentials the CP presents to the node. If you find yourself needing one, stop
and redesign, because the alternative is a second network leg for management
and a permanent hole in the tenant's segment.

## Reach your control plane through a door, not a second leg

An overlay guest reaches services of the installation through a host-local
proxy in its own VNI, addressed at `169.254.169.254`. A `ProxyService` names
your control plane as the destination — an object, not a URL — and the guest's
agent talks to it there.

OpenStack Trove, the closest architecture to this one, instead gives every
instance a second NIC on a management network. We do not need to, and the
difference is worth stating plainly rather than discovering: with one leg, a
door that is missing or misconfigured makes the node unmanageable, where a
separate management NIC fails independently of the tenant's network. One leg is
simpler and it concentrates the risk — take it deliberately.

## Say where the nodes go, and say it with `interfaces`

A `NodeSet` carries an `interfaces` template, and every replica it generates
gets it (`gen_nodes`). One entry per leg; pin the **subnet**, not the network —
a network with more than one placeable subnet is ambiguous and is refused with
the candidates listed.

!!! warning "Not built yet"
    The model a PaaS sends — `infra.NodeSet` in `gcl_sdk` — carries only the
    legacy `default_network` dict in its target fields, so today an element can
    express at most a single legacy network pin, resolved to the first
    candidate with a warning. Adding `interfaces` there is the first step, and
    it has to ship to agents before the control plane starts sending it.

## Read `default_network` as what it is

`default_network` is not "the node's network". It is the **promoted scalar of
the primary leg** — the port at index 0 — kept because every published element
reads `…:default_network:ipv4` and a node with one leg should not have to say
anything. Read it for the address; do not infer from it that there is exactly
one leg, and do not write placement into it in new code.

Index 0 is the primary leg. Index also means device order in the guest, which
is *not* implemented today — ports are handed to the hypervisor in whatever
order the query returns.

## The port is yours; the address space is the tenant's

When you put a node on a tenant's subnet, keep the node in **your** project and
let its port sit in **their** subnet. AWS names this exactly: a
*requester-managed network interface* is one a service creates in your network
on your behalf and whose lifecycle you do not control.

The reason is not tidiness. A node in the tenant's project is a node the tenant
can delete — which means they can destroy their own managed database through
the compute API, and your control plane finds out afterwards. Keeping the node
yours also keeps quota and accounting where the service is.

Ports already work this way: a port belongs to its **node's** project, not to
its subnet's.

## Consent comes from the tenant's own call, not from your confidence

The rule to build against:

> a service may place a port in the subnet **its caller could have used**

The permission gives your service the *capability*; the tenant naming their
subnet in their own authenticated request supplies the *authorisation*. This is
how AWS works too — RDS may place interfaces, and the customer's DB subnet
group is what says where.

Do not treat "my service holds the permission" as sufficient. A service that
may place a port in *any* subnet will eventually place one in the wrong
tenant's, and nothing will have said no.

In practice that is `compute.node.foreign_subnet` (and the node-set twin),
enforced when the declaration is written. It is checked on the HTTP path,
because that is the only place with a caller: by the time the compiler builds
the port there is no token to ask about, only a declaration to execute. The
manifest path writes the model directly and is admin-only.

The model-level tenancy boundary still stands behind it and asks the narrower
question it *can* answer without IAM — is there a node declaring this leg — so
a port made by hand into a foreign private subnet is refused as it always was.

## The tenant can see your port, and should

The owner of an overlay subnet sees every port on it, including yours, and
cannot modify or delete them — `update` and `delete` force the caller's project
into their own lookup, so read access widens nothing that can be written.

This is not decoration. Without it an address disappears from the tenant's own
subnet with nothing to point at, and `Subnet.delete` — which guards on *every*
port, not just the caller's — refuses with "still has 1 port(s); delete them
first" about a port they cannot see and could not delete.

Expect your ports to be visible, and name them so that what shows up is
legible.

## The port carries the service's groups, not the tenant's

A port's citations are checked against **its own** project, and a PaaS node's
port belongs to the PaaS. So put the rules the instance needs in a security
group of *your* project and cite that; the tenant does not attach theirs.

Two consequences worth knowing before you meet them:

- The port is still in its subnet's default identity group — that membership
  is decided by the compiler rather than cited by uuid, so it costs nothing and
  crosses no project boundary.
- `rule.remote_group` **is** checked against the project, so your rules can
  admit callers by CIDR (the tenant's subnet, specific addresses) but cannot
  name an identity group of the tenant's. "Let in exactly their application
  servers" is not expressible yet; "let in their subnet" is.

## Security groups have to hold where the master will be, not where it is

Whatever your rules admit — and per the section above that is a CIDR, not the
tenant's group — make sure they hold for **every** subnet a replica may land
in, not just the one the master is in today. Yandex documents the failure this
prevents: incomplete rules cost you access to the cluster *at failover*, which
is exactly when nobody is reading documentation.

## Plan addresses for the service, not for the instance

The subnet is sized for what the service will put in it over time. A
highly-available instance takes several addresses, replicas take more, and
platform reservations take a few. Azure's minimum for an injected database
subnet is a /28, of which five are reserved and one HA server uses four — a
useful order of magnitude even though our numbers differ.

Placement is close to irreversible, in both the industry's implementations and
ours: a node cannot be moved to another subnet after it is built, and changing
a leg's `index` is a delete-and-create, because the compiler identifies a
declared leg by its index. If the leg pins an address it survives the rebuild;
otherwise it comes back different.

## What not to do

- **Do not create the node in the tenant's project.** They can delete it.
- **Do not publish the network** (`access=public`) to get access. It opens
  every subnet of it, management ranges included, to every project.
- **Do not build a TCP relay for the data path.** The door is right for the
  control path and wrong for a database session: a per-VNI userspace hop in
  front of every connection is a different latency and availability story than
  a flow through OVS. If a projected endpoint is ever wanted, it should be
  designed as one, not grown out of the proxy.

## Status

| | |
|---|---|
| control path as a pull | dbaas already works this way |
| door onto the paas control plane | built; no CLI for it yet |
| `NodeSet.interfaces` → replicas | built in the core |
| `interfaces` in what a PaaS sends | **not built** |
| tenant sees foreign ports on their overlay subnet | built |
| permission to declare a leg in a foreign subnet | built |
| network in the order (`PGInstance`) | **not built** |

## What the other clouds do

Two families, and every provider is in one of them.

### Injection: the database's interface is in the tenant's subnet

| | how | who owns the interface |
|---|---|---|
| **AWS RDS** | a *requester-managed* ENI in a subnet of the customer's DB subnet group | AWS creates it in the customer's VPC; the customer can view and tag it, but cannot modify it, and it is detached and deleted with the DB |
| **Azure Flexible Server** (VNet integration) | injected into a subnet *delegated* to `Microsoft.DBforPostgreSQL/flexibleServers` — no other resource type may live there | the service; the subnet is the customer's but is given over to it |
| **Yandex MDB PostgreSQL** | hosts placed directly in the customer's subnets | the service, in the customer's folder network |
| **OpenStack Trove** | **two** NICs per instance: one on the tenant network for database traffic, one on a management network for the guest agent | the tenant's port is the tenant's; the management port is the operator's |

The interesting one is AWS, because it names the thing exactly: a
*requester-managed network interface* is an interface **a service creates in
your network on your behalf**, and whose lifecycle you do not control. The
tenant's subnet is the address space; the service is the owner.

Azure's delegation is the same idea approached from the other end — rather than
marking the interface, mark the subnet: this subnet is given to the database
service, and nothing else may be placed in it.

### Endpoint projection: the database stays put, a representative appears

| | how |
|---|---|
| **Azure Private Link** | the server stays outside the VNet; a private endpoint NIC representing it is created inside, and DNS resolves to that |
| **GCP Private Service Connect** | an internal endpoint in the consumer VPC, a forwarding rule pointing at the instance's service attachment |
| **GCP Private Services Access** | the older shape: the instance lives in a Google-managed producer VPC *peered* with the customer's, out of a range the customer allocates |

Google recommends Private Service Connect over the peered model, mostly because
peering is not transitive: one instance reachable from several consumer
networks is awkward under peering and natural under endpoints.

Azure supports both families but not at once — a server is injected *or*
endpointed, decided at creation, and an injected server cannot use Private Link.

Of the two families this platform built injection, in AWS's shape: the node
stays in the service's project and its port sits in the tenant's subnet
(`NodeSet.interfaces`, gated by `compute.node.foreign_subnet`), and the
control path goes through a door rather than a second leg. Endpoint projection
is not built — the door is HTTP, and a database session is raw TCP (see "What
not to do").

Sources:

- AWS, [Requester-managed network interfaces](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/requester-managed-eni.html)
  and [Working with a DB instance in a VPC](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_VPC.WorkingWithRDSInstanceinaVPC.html)
- Microsoft, [Network with private access (virtual network integration)](https://learn.microsoft.com/en-us/azure/postgresql/network/concepts-networking-private)
  and [Networking with Private Link](https://learn.microsoft.com/en-us/azure/postgresql/network/concepts-networking-private-link)
- Google, [Private Service Connect overview for Cloud SQL](https://docs.cloud.google.com/sql/docs/postgres/about-private-service-connect)
  and [Configure private services access and Private Service Connect](https://docs.cloud.google.com/sql/docs/postgres/configure-private-services-access-and-private-service-connect)
- Yandex Cloud, [DB network and clusters](https://yandex.cloud/en/docs/managed-postgresql/concepts/network)
  and [Security groups](https://yandex.cloud/en/docs/vpc/concepts/security-groups)
- OpenStack, [Running Trove in production](https://docs.openstack.org/trove/latest/admin/run_trove_in_production.html)

See also: [node interfaces](node-interfaces.md),
[private networks](private-network.md),
[security groups across a network](security-group-remote-access.md).
