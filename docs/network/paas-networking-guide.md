---
icon: lucide/network
title: Networking for a managed service
---

For the canonical API, see [SDN networking resources](sdn-api-contract.md).
This guide describes the networking primitives available to a managed service;
it does not add network placement to the DBaaS ordering API.

## Control traffic

An agent in a private overlay initiates requests to its control plane.
A `ProxyService` exposes one HTTP service at `169.254.169.254:<port>` inside
one Network. An optional domain resolves to that metadata address.

```yaml
$core.network.catalog.proxy_services:
  control_plane:
    project_id: "00000000-0000-0000-0000-000000000000"
    network: $core.network.networks.$private:uuid
    port: 11020
    destination:
      kind: node
      node: $core.compute.nodes.$control_plane:uuid
    domain: paas.internal
```

Replace the example project UUID with the service project. Its references assume
these Node and Network resources are declared in the same manifest.

A node destination must belong to the service's project. A destination with
`kind: address` is operator-only and needs
`network.proxy_service.custom_destination`. Opening a service on a non-private
Network requires that permission too.

The proxy stamps `X-Genesis-Node-UUID` from the fabric and removes the guest's
copy and forwarding claims. The upstream must authenticate requests itself;
the UUID header is context, not a credential. Redirects are relayed without
following them. Path traversal is rejected. Direct access to the upstream's
real address is not the ProxyService interface.

ProxyService is HTTP; it is not a private TCP endpoint for database sessions.

## Data interfaces

Create canonical Ports and bind them through `Port.node`. A Node can be
created with `networking: []` so that a separately declared Port owns its
attachment. Node creation can alternatively use inline `networking` entries;
that wrapper is create-only. Later operations address Port, Address and
SG/IG binding resources directly.

A NodeSet accepts a `networking` template. Replicas allocate their own
Ports/IPs/MACs/FIPs. Existing Port UUIDs and explicit addresses are not valid
in that template. Changing the template affects new replicas and preserves
existing replicas and external policy bindings.

Attachment allocates the lowest free index when omitted. Index 0 is the
primary interface for boot/image operations. An attached index is immutable;
detach clears it without renumbering other interfaces. Live and persistent
libvirt NIC hotplug and withdrawal are supported. Subnet/IP/MAC allocation
stays with the Port during detach and cross-host attachment.

## Tenant ownership

Canonical Port and its Node belong to one project; SG, IG and floating Address
bindings also stay in that project. A canonical Port cannot cite another
project's private subnet. A shared/public pool provides controlled address
allocation, not cross-project ownership of a private interface.

The older `interfaces` declarations have a separate privileged foreign-subnet
path. That path does not authorize foreign-subnet placement through the new
canonical Port API. Do not assume a service can inject a canonical interface
into an arbitrary tenant's private network today.

A future DBaaS order needs explicit tenant consent, service ownership and
lifecycle rules before exposing that placement. Network selection in the
DBaaS order and private TCP endpoint projection are outside this SDN change.

## Policy and external access

Attach SGs through `PortSecurityGroup`, not an inline mutable Port list.
No SG bindings means unrestricted SG filtering; a bound empty SG denies all.
Port anti-spoofing remains enabled independently. Rules combine as allow rules;
`remote_group` refers to an IdentityGroup. The automatic identity group covers
the entire Network, including its subnets; `network_identity: false` opts out.

A floating Address binds through `Address.port`, can be declared independently,
and can allocate from an approved pool by Network or Subnet. Its ACTIVE status
requires the assigned host's acknowledgement. Detach/delete waits for withdrawal
before reusing the address on another host.

Border is a separate gateway resource. It reconciles SNAT/masquerade and DNAT
forwards either on Core's agent, a dedicated gateway VM, or an explicitly named
node. External reachability still requires the installation to route traffic
to that gateway. A managed realm uses its Border for the nested network; its
outer private Port/FIP and SG policy remain separate resources.

## Delivery

Install the tested SDK/Restalchemy/Core set and update SDN host agents before
sending the new multi-interface and withdrawal contracts. Flat hosts keep their
existing single-interface wire format. See the contract's delivery section for
the package release order.
