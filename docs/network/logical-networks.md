# Logical networks and subnets

Network groups private subnets across hypervisors and clouds. Its CIDR is an
allocation pool. Subnet selects hypervisors by UUID; cloud credentials and
optional gateway defaults belong to those hypervisors. NodeSets select Network.

## Create a network and subnet

`POST /v1/network/networks/`:

```json
{
  "project_id": "<CORE_PROJECT_ID>",
  "name": "application",
  "cidr": "10.80.0.0/16",
  "connectivity": {
    "driver": "auto",
    "protocol": "tcp",
    "port": 443,
    "mtu": 1380
  }
}
```

The IPv4 pool must be at least a `/23`. Default connectivity is `auto`, TCP/443,
MTU 1380. Network CIDR, project and connectivity are immutable.

`POST /v1/network/subnets/`:

```json
{
  "network": "<NETWORK_ID>",
  "hypervisors": [
    "<HYPERVISOR_ID>"
  ],
  "dns_servers": [
    "1.1.1.1"
  ]
}
```

Core allocates a free `/23` and a default route through its first usable address.
An explicit CIDR must be a free `/23` inside the pool. DNS belongs to each subnet.
Allocation survives metadata changes and is released only after subnet cleanup.

A cloud subnet names exactly one `kind: "openstack"` hypervisor in the network's
Core project. A local SDN subnet can name several compatible `libvirt` or
`exordos_local_hyper` hypervisors with OVS enabled in one SDN fabric. This allows
scheduling on any selected host. An OpenStack hypervisor describes a provider
project and region rather than an individual Nova host.

## Import an existing subnet

`PUT /v1/network/subnets/<SUBNET_ID>`:

```json
{
  "network": "<NETWORK_ID>",
  "hypervisors": [
    "<ONPREM_HOST_A>",
    "<ONPREM_HOST_B>"
  ],
  "gateway": {
    "role": "client",
    "external_subnet": "<TRANSPORT_SUBNET_ID>",
    "gateway_image": "https://images.example/gateway.raw.zst"
  }
}
```

CIDR, addresses and workloads are preserved. An imported CIDR can be outside the
allocation pool but must not overlap another member. The backing driver must
match the chosen hypervisors. Removing an imported subnet from the logical
network preserves its original subnet and network.

## Connectivity

`auto` connects new subnets of the same cloud hypervisor through one Neutron
router; their external network must match. Other pairs, including imports, use
an owned CloudNetworkLink with managed OpenVPN gateways.
`connectivity.driver: "openvpn"` uses OpenVPN for every pair.

Configure `Hypervisor.network_defaults` before adding subnets, or override it
through `Subnet.gateway`. Cloud options are `external_network`, `gateway_image`,
`role`, `gateway_cores` and `gateway_ram`. Local options are `external_subnet`,
`gateway_image`, `gateway_disk`, the same role and resource options, or an existing
`agent` with an optional reachable `endpoint`.
See [OpenStack multicloud](openstack-multicloud.md) for gateway requirements.

Effective defaults must agree across hosts of one local subnet; an explicit
subnet override resolves differences. Defaults are captured at attachment.
Placement, CIDR and provider backing are immutable. Gateway settings can change
before an endpoint is bound, provided the provider backing stays the same.

Roles are `auto`, `server` and `client`. On-premises behind NAT can initiate
connections to an OpenStack gateway's floating IP. Every pair needs an accepting
side: two explicit clients or two explicit servers are rejected. Connections
form a direct mesh without transit or L2 extension. Core owns the automatic
link; manage its membership through subnets.

Network and managed-subnet administration additionally requires the
corresponding `compute.hypervisor` permission because it manages infrastructure.
Workload allocation follows the usual project and public-network access rules.

## Workload placement

A NodeSet selects the logical network:

```json
{
  "default_network": {
    "network": "<NETWORK_ID>"
  },
  "placement": {
    "kind": "anti-affinity",
    "scope": "site"
  }
}
```

The scheduler jointly selects an ACTIVE subnet and an eligible hypervisor,
then creates its primary port. Hypervisor `site` is an optional failure-domain
label; without it the hypervisor UUID is the domain. Several physical
hypervisors may share a site. Subnets do not duplicate this label.

`placement.kind` is `anti-affinity` (required separation) or
`soft-anti-affinity` (prefer separation); `scope` is `site` or `pool`.
Logical-network NodeSets default to soft anti-affinity across sites. Hard
anti-affinity leaves replicas pending when domains are exhausted.
Adding capacity does not migrate existing nodes.

For `disk_spec.kind: "disks"`, omitted `ephemeral` defaults to `false` if
the network includes cloud subnets. Explicit disk policies are preserved.
Security groups control traffic; remote-group rules across the network use
observed member IPs. Explicit workload IPs and automatic floating-IP allocation
are unavailable with logical-network placement. Configure public entry points
separately.

## Observe and remove

`GET /v1/network/subnets/?network=<NETWORK_ID>` returns members with their logical
`network`, `hypervisors`, allocated `cidr`, `status` and `error`.
`Network.driver_state.subnets` summarizes observations by subnet UUID.

Delete a member through `DELETE /v1/network/subnets/<SUBNET_ID>`. Cleanup is
asynchronous; failures retain DELETING records for retry. Workloads, user address
reservations and foreign links block removal. Network deletion uses the same
cleanup and is refused while Nodes or NodeSets reference it. Manage generated
backing resources through the logical network and subnet.

## CLI

```sh
exordos network networks add --name application --project-id "$PROJECT_ID" \
  --cidr 10.80.0.0/16 --connectivity auto
exordos network subnets add --name application-cloud --project-id "$PROJECT_ID" \
  --network "$NETWORK_ID" --hypervisor "$CLOUD_ID"
exordos network subnets update "$ONPREM_SUBNET_ID" --network "$NETWORK_ID" \
  --hypervisor "$HOST_A" --hypervisor "$HOST_B" --gateway-file gateway.json
exordos compute sets add --project-id "$PROJECT_ID" --image "$IMAGE" \
  --network "$NETWORK_ID" --durable --replicas 2 \
  --placement-kind anti-affinity --placement-scope site
```
