# Connect on-premises to OpenStack

This procedure adds an OpenStack account to an existing Core installation and
connects its private network to on-premises. The on-premises gateway initiates
OpenVPN connections; it can use a private address behind NAT. The cloud gateway
accepts connections on a floating IP. Cloud load balancers reach on-premises
backends through the established tunnel.

[Русская версия](onprem-openstack.ru.md).

## Prerequisites

- Install compatible Core, SDK and SeedOS images with cloud drivers and
  `OpenVPNCapabilityDriver`. Core and gateway hosts need OpenVPN 2.6+.
- Obtain a project-scoped OpenStack application credential, or a username,
  password, domain and project. It needs Nova, Neutron, Cinder and Glance
  permissions. Submit it to Core's API; only Core retains the credentials.
- Ensure Core reaches the Keystone catalog endpoints independently of the
  link. Gateway bootstrap needs access to package repositories.
- Choose non-overlapping private CIDRs. The examples use `10.80.0.0/24` in
  OpenStack; replace it if it overlaps any connected network.
- For a managed on-premises gateway, use an existing `ovs_evpn` subnet and a
  placeable `flat_bridge` transport subnet with outbound access to the cloud
  gateway on TCP/443. The transport subnet belongs to the same Core project,
  has a default gateway and has `next_server` unset. Its IP can be private.
- Prepare an on-premises gateway image with Core guest/render/service agents
  and an Ubuntu cloud-init image in Glance for the cloud gateway. Provide Nova
  flavors matching gateway resources (default 1 vCPU, 1024 MiB RAM) and the
  resources requested for workloads.

`PROJECT_ID` below is the Core project UUID, not the OpenStack project UUID.
`ONPREM_SUBNET_ID` and `ONPREM_TRANSPORT_SUBNET_ID` are existing Core subnet
UUIDs. `OS_EXTERNAL_NETWORK_ID` and `OS_GATEWAY_IMAGE_ID` are provider UUIDs.

Replace angle-bracket parameters and example URLs with actual values.
All requests below go to Core's authenticated user API. Connection and link
administration require the compute hypervisor policy. Send each JSON body to
its indicated endpoint and record the returned `uuid`, for example:

```sh
curl -fsS "$CORE/v1/compute/cloud_connections/" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' --data-binary @connection.json
```

## Register the account

`POST /v1/compute/cloud_connections/`; record `CONNECTION_ID`:

```json
{
  "project_id": "<PROJECT_ID>",
  "name": "remote-openstack",
  "driver_spec": {
    "kind": "openstack",
    "auth_url": "https://identity.example/v3",
    "auth_type": "v3password",
    "auth": {
      "username": "<OS_USERNAME>",
      "password": "<OS_PASSWORD>",
      "user_domain_name": "<OS_USER_DOMAIN_NAME>",
      "project_id": "<OS_PROJECT_ID>"
    },
    "region": "RegionOne"
  }
}
```

The `driver_spec.kind` discriminator selects the driver schema. The
`openstack` kind holds its Keystone credentials and catalog settings. Core
records the authenticated project in read-only `driver_state.project_id`. The
`driver_spec.auth` object is write-only: create, show, list and update responses omit it.
No provider configuration files or environment variables are needed on Core.

The same registration through CLI prompts for the password without echoing it
when `OS_PASSWORD` is not supplied in the CLI environment:

```sh
exordos compute cloud-connections add --name remote-openstack --kind openstack \
  --project-id "$PROJECT_ID" --auth-url https://identity.example/v3 \
  --auth-type v3password --username "$OS_USERNAME" \
  --os-project-id "$OS_PROJECT_ID" --user-domain-name "$OS_USER_DOMAIN_NAME" \
  --region RegionOne
```

`--project-id` names the Core project; `driver_spec.auth.project_id` and `--os-project-id`
name the OpenStack project. Obtain the username, user domain, project ID and
Keystone URL from the provider's API access settings. With a project name,
use `project_name` and `project_domain_name` instead of `driver_spec.auth.project_id`.

Replace the password with `PUT` containing the complete `driver_spec`, including
its `kind` and updated `auth`. The CLI reads existing public settings automatically:

```sh
exordos compute cloud-connections update-credentials "$CONNECTION_ID" \
  --auth-type v3password --username "$OS_USERNAME" \
  --os-project-id "$OS_PROJECT_ID" --user-domain-name "$OS_USER_DOMAIN_NAME"
```

Core authenticates the replacement and rejects a different provider project.
Auth URL, type, region and interface are immutable; use a different connection
for a different cloud scope.

Application credentials are an alternative when the provider supports them:
set `driver_spec.auth_type: "v3applicationcredential"` with `application_credential_id`
and `application_credential_secret` in `driver_spec.auth`. The CLI accepts
`--application-credential-id` and prompts for its secret.

Core derives agent Orchestrator/Status URLs from its installation's combined
Boot API endpoint and pins the installed SDK version for bootstrap. These are
not cloud connection parameters. This service address must be reachable from
the connected network; a management network needs its own attachment and
return route. Use a service address separate from the link gateway host,
whose own listeners reject new tunnel connections. Local agents must reach
Core independently of the cloud link. Bootstrap needs access to the package
repository and SDK dependencies.

## Create cloud networking and compute capacity

`POST /v1/network/networks/`; record `CLOUD_NETWORK_ID`:

```json
{
  "project_id": "<PROJECT_ID>",
  "name": "cloud-private",
  "driver_spec": {
    "driver": "cloud",
    "connection": "<CONNECTION_ID>",
    "external_network": "<OS_EXTERNAL_NETWORK_ID>"
  }
}
```

`POST /v1/network/subnets/`; record `CLOUD_SUBNET_ID`:

```json
{
  "project_id": "<PROJECT_ID>",
  "name": "cloud-private",
  "network": "<CLOUD_NETWORK_ID>",
  "cidr": "10.80.0.0/24",
  "dns_servers": ["1.1.1.1"],
  "routers": [{"to": "0.0.0.0/0", "via": "10.80.0.1"}]
}
```

Use resolvers appropriate for the installation. Core creates owned Neutron
networking and a router to the external network; it does not adopt existing
unmarked networks or VMs.

`POST /v1/compute/hypervisors/` registers the cloud pool:

```json
{
  "name": "remote-openstack",
  "driver_spec": {
    "kind": "cloud",
    "connection": "<CONNECTION_ID>",
    "volume_type": "__DEFAULT__"
  }
}
```

One pool consumes the connection's shared quotas. For SSH through an existing
OpenStack keypair, optionally set `key_name` in this driver specification.

## Connect the private networks

`POST /v1/compute/cloud_network_links/`; record `LINK_ID`:

```json
{
  "project_id": "<PROJECT_ID>",
  "name": "onprem-cloud",
  "transport": {"driver": "openvpn", "protocol": "tcp", "port": 443},
  "endpoints": [
    {
      "subnet": "<ONPREM_SUBNET_ID>",
      "role": "client",
      "external_subnet": "<ONPREM_TRANSPORT_SUBNET_ID>",
      "gateway_image": "https://images.example/onprem-gateway.raw.zst"
    },
    {
      "subnet": "<CLOUD_SUBNET_ID>",
      "role": "server",
      "external_network": "<OS_EXTERNAL_NETWORK_ID>",
      "gateway_image": "<OS_GATEWAY_IMAGE_ID>"
    }
  ],
  "mtu": 1380
}
```

Core creates both gateways and routes. The on-premises gateway dials the
cloud floating IP. No inbound port forwarding to on-premises is needed.
The cloud transport bootstraps before guest access to Core is available.
Poll `GET /v1/compute/cloud_network_links/<LINK_ID>` until `ACTIVE`, then verify
workload traffic in both directions. Existing VMs must renew DHCP for routes.

For a pre-existing on-premises routing gateway, replace its endpoint with:

```json
{"subnet": "<ONPREM_SUBNET_ID>", "agent": "<GATEWAY_AGENT_ID>", "role": "client"}
```

Its SDK agent must advertise `cloud_net_link`; the operator supplies local
subnet routes and its SDN attachment. Core supplies the VPN configuration.

## Use the cloud

Create ordinary Core Nodes in the cloud subnet. For example,
`POST /v1/compute/nodes/`:

```json
{
  "project_id": "<PROJECT_ID>",
  "name": "cloud-lb-1",
  "cores": 2,
  "ram": 2048,
  "default_network": {"subnet": "<CLOUD_SUBNET_ID>"},
  "disk_spec": {
    "kind": "root_disk",
    "image": "<compatible Glance image ID or allowed image URL>",
    "size": 20,
    "ephemeral": false
  }
}
```

The scheduler selects the cloud pool from the subnet's connection. Core creates
Nova machines and durable Cinder disks. Use the same `default_network.subnet`
for NodeSets. Configure load balancers with on-premises private backend IPs.
Configure OpenStack ingress for a public frontend, for example a floating IP
on the load balancer VM's port. Creating a Node does not publish it. The link
gateway's floating IP serves the VPN and does not publish the load balancer.
Application security groups must permit frontend/backend traffic and replies;
the link retains original private addresses. Additional durable disks use
`ephemeral: false`. URL images must match `[openstack_cloud] image_origins`.

Observe Nodes, Ports and Volumes through the usual Core APIs. `ACTIVE` on a
link describes transport health, so check the application separately. Add
further sites with a `PUT` of the full endpoint list. Keep CIDRs disjoint;
two forced clients or two forced servers cannot form a full-mesh pair.

To remove the integration, remove workloads and the link, wait for link cleanup
to finish, then delete unused pools, subnets, networks and the connection.
