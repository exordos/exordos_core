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

Service-owned NodeSets can select a customer's default network with
`default_network: {project_id: CUSTOMER_PROJECT_UUID}`, or select an explicit
`network` or `subnet`. The caller needs `network.port.use_foreign_subnet` to
use another project's private subnet or default placement. The installation
grants this permission to trusted service users; it is not part of the project
owner role. Port and SG ownership stays in the service project.

Core stores the selected Subnet in read-only `NodeSet.network_subnet` for these
requests. Scale and replacement use that binding without reselecting the
project's default network. Existing NodeSets are pinned to their existing
primary Subnet by the migration when all their primary Ports use one Subnet.
Foreign Ports are visible to the owner of their overlay Subnet for reading;
the owner cannot edit or delete the service's Ports.

`default_network.security_groups` lists SG UUIDs from the NodeSet's project.
Overlay replica creation binds them atomically with the Port and enables
`enforce_security_group`. A missing or invalid group rolls creation back.
Flat replicas retain legacy networking and do not receive overlay SG bindings.

Default placement uses the same `legacy_flat_default` policy as ordinary Nodes.
A service selecting a customer project uses that customer's flat/default
placement, while its infrastructure remains service-owned. A pinned flat
cluster keeps its Subnet when compatibility is disabled. Subnet deletion is
blocked while Ports, Address reservations, or a pinned NodeSet reference it.

Set NodeSet `default_network` to select the private Network and optionally request
one FIP per replica with `auto_floating_ip`. The template applies to new replicas;
existing replicas retain their attachments. Read private/FIP addresses from
`nodes[uuid].ipv4` and `.floating_ip`. Manage replica Ports through NodeSet.

Private Port, SG bindings and FIP belong to the NodeSet's service project;
replica Nodes use the existing internal NodeSet project. Shared external
pools permit address allocation without granting access to another project's
private network. Policy uses independent SecurityGroupRule and PortSecurityGroup
resources; [remote SGs](security-group-remote-access.md) match only same-network
members.

For inbound access, bind a floating Address or use an LB. Border is a separate
SNAT/DNAT gateway when the service needs one; overlay egress already has
host-local NAT. A nested realm runs its own network stack, while its outer
Port/FIP remains part of the parent installation.
