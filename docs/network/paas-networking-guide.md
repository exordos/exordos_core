# Networking for a managed service

A managed service uses the same [Node/NodeSet and Port API](sdn-api-contract.md)
as other workloads. Its database sessions use ordinary private networking;
ProxyService provides HTTP access to its control plane.

## Control traffic

An agent in an isolated network can reach a declared HTTP service at
`169.254.169.254:<port>`. An optional domain resolves to that address:

```yaml
$core.network.catalog.proxy_services:
  control_plane:
    project_id: SERVICE_PROJECT_UUID
    network: $core.network.networks.$private:uuid
    port: 11020
    destination:
      kind: node
      node: $core.compute.nodes.$control_plane:uuid
    domain: paas.internal
```

Node destinations belong to the service's project. Bare-address destinations
and doors on non-private Networks require
`network.proxy_service.custom_destination`.

The proxy replaces caller-supplied identity/forwarding headers and stamps
`X-Genesis-Node-UUID` from the fabric. The upstream must authenticate requests;
that UUID is context, not a credential. Redirects are relayed without being
followed. This is HTTP proxying, not a database TCP endpoint.

## Replica networking

Set NodeSet `default_network` to select the private Network and optionally request
one FIP per replica with `auto_floating_ip`. The template applies to new replicas;
existing replicas retain their attachments. Read private/FIP addresses from
`nodes[uuid].ipv4` and `.floating_ip`. Manage replica Ports through NodeSet.

Node, private Port, SG bindings and FIP belong to one project. Shared external
pools permit address allocation without granting access to another project's
private network. Policy uses independent SecurityGroupRule and PortSecurityGroup
resources; [remote SGs](security-group-remote-access.md) match only same-network
members.

For inbound access, bind a floating Address or use an LB. Border is a separate
SNAT/DNAT gateway when the service needs one; overlay egress already has
host-local NAT. A nested realm runs its own network stack, while its outer
Port/FIP remains part of the parent installation.
