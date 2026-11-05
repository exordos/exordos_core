# Connect on-premises to OpenStack

One logical network joins the existing on-premises subnet and new cloud
subnets. An on-premises client behind NAT dials a cloud server's floating IP.
Cloud load balancers reach on-premises private backends through this tunnel.

[Русская версия](onprem-openstack.ru.md).

## Prepare the installation

Install compatible Core, SDK and SeedOS with the OpenStack extra and
`OpenVPNCapabilityDriver`. Core and gateways need OpenVPN 2.6+.
Core must reach Keystone catalog endpoints independently of the private link;
bootstrap needs access to package repositories. No provider configuration file
is required on Core.

Obtain a Keystone URL, username, password, domain and project with Nova, Neutron,
Glance and Cinder permissions. Prepare an Ubuntu cloud-init gateway image in
Glance and matching Nova flavors (default gateway: 1 vCPU, 1024 MiB RAM).
The local gateway image needs Core guest/render/service agents.
Use an existing OVS/EVPN private subnet and a placeable `flat_bridge` transport
subnet in the same Core project, with a default gateway and no `next_server`.
It must reach the cloud gateway on TCP/443 and can have a private address.

All requests go to Core's authenticated user API. Infrastructure operations
require the corresponding compute hypervisor permission.
Core and OpenStack project UUIDs are distinct. Replace placeholders with actual
values and save the returned UUID from each request. Never commit credentials.

## Register a cloud hypervisor

`POST /v1/compute/hypervisors/`; save `CLOUD_HYPERVISOR_ID`:

```json
{
  "project_id": "<CORE_PROJECT_ID>",
  "name": "remote-openstack",
  "driver_spec": {
    "kind": "openstack",
    "auth_url": "https://identity.example/v3",
    "auth_type": "v3password",
    "auth": {
      "username": "<OS_USERNAME>",
      "password": "<OS_PASSWORD>",
      "user_domain_name": "Default",
      "project_id": "<OS_PROJECT_ID>"
    },
    "region": "RegionOne",
    "volume_type": "__DEFAULT__"
  },
  "network_defaults": {
    "external_network": "<OS_EXTERNAL_NETWORK_ID>",
    "gateway_image": "<OS_GATEWAY_IMAGE_ID>",
    "role": "server"
  }
}
```

This hypervisor represents the provider project and region. Core authenticates
it and records provider identity in read-only `driver_state`.
`driver_spec.auth` is write-only and is omitted from responses and agent targets.
`network_defaults` supplies gateway settings for subnets attached to this cloud.
For an existing OpenStack keypair, optionally set `driver_spec.key_name`.

CLI registration and credential replacement:

```sh
exordos compute hypervisors add --name remote-openstack --kind openstack \
  --project-id "$PROJECT_ID" --auth-url https://identity.example/v3 \
  --auth-type v3password --username "$OS_USERNAME" \
  --os-project-id "$OS_PROJECT_ID" --user-domain-name Default \
  --region RegionOne --network-defaults-file cloud-network-defaults.json
exordos compute hypervisors update-credentials "$CLOUD_HYPERVISOR_ID" \
  --auth-type v3password --username "$OS_USERNAME" \
  --os-project-id "$OS_PROJECT_ID" --user-domain-name Default
```

The defaults file contains the example's `network_defaults` object.
The CLI prompts for the password without echo when `OS_PASSWORD` is absent.
`--project-id` is the Core project; `--os-project-id` is the OpenStack project.
To choose the provider project by name, use `project_name` and
`project_domain_name` in API auth, or the corresponding CLI options.

API credential replacement uses `POST /v1/compute/hypervisors/<uuid>/actions/update_credentials/invoke`
with `auth_type` and new `auth`. The action preserves concurrently updated driver settings. Password and application-credential methods can be exchanged if
the authenticated provider identity stays the same. Auth URL, region and
interface are immutable. Application credentials use
`auth_type: "v3applicationcredential"` and `application_credential_id` plus
`application_credential_secret` inside `auth`, when supported by the provider.

Change public driver settings without resending credentials:

```sh
exordos compute hypervisors update "$CLOUD_HYPERVISOR_ID" --driver-spec volume_type=fast
```

OpenStack partial edits accept `volume_type` and `key_name`, separately from
name/site updates. They use `POST /v1/compute/hypervisors/<uuid>/actions/update_driver_settings/invoke`
with `{"settings": {"volume_type": "fast"}}`; saved credentials stay on Core.
Finite quotas retain their provider values. Unlimited quotas (`-1`) are represented
by the largest supported counter value; quota availability is not physical capacity.

Agent Orchestrator/Status addresses use the host advertised by
the installation's Boot API and the configured `orch_api.bind_port` and
`status_api.bind_port`. Publish both HTTP runtime API ports on that host.
The agent protocol encrypts runtime requests and responses with the node key.
Agents need a reachable service address and return route.
The service address must be separate from a link gateway's host services, which
reject new connections from the tunnel. Provider access and local agents'
access to Core must survive loss of the cloud link.

## Create the logical network

`POST /v1/network/networks/`; save `NETWORK_ID`:

```json
{
  "project_id": "<CORE_PROJECT_ID>",
  "name": "application",
  "cidr": "10.80.0.0/16"
}
```

The CIDR is a pool for new `/23` subnets. Choose non-overlapping address ranges
across all connected subnets, including the existing on-premises range.

`PUT /v1/network/subnets/<ONPREM_SUBNET_ID>`:

```json
{
  "network": "<NETWORK_ID>",
  "hypervisors": [
    "<ONPREM_HYPERVISOR_ID>"
  ],
  "gateway": {
    "role": "client",
    "external_subnet": "<ONPREM_TRANSPORT_SUBNET_ID>",
    "gateway_image": "https://images.example/onprem-gateway.raw.zst"
  }
}
```

The existing subnet and workloads keep their addresses. List all compatible
on-premises hypervisors that may host its VMs. Values in `gateway` can instead
be configured in their `network_defaults`.

`POST /v1/network/subnets/`; save `CLOUD_SUBNET_ID`:

```json
{
  "network": "<NETWORK_ID>",
  "hypervisors": [
    "<CLOUD_HYPERVISOR_ID>"
  ],
  "dns_servers": [
    "1.1.1.1"
  ]
}
```

Core allocates a `/23`, creates owned Neutron networking and manages the link.
The on-premises gateway initiates it; no inbound NAT forwarding is required.
No separate CloudNetworkLink request is necessary. Poll the subnet API until
members are ACTIVE, then test application traffic in both directions.
Existing guests must renew DHCP for added peer routes.

## Run workloads

To create a load balancer in the logical network, use
`POST /v1/compute/nodes/`:

```json
{
  "project_id": "<CORE_PROJECT_ID>",
  "name": "cloud-lb-1",
  "cores": 2,
  "ram": 2048,
  "default_network": {
    "network": "<NETWORK_ID>"
  },
  "disk_spec": {
    "kind": "root_disk",
    "image": "<GLANCE_IMAGE_ID_OR_ALLOWED_URL>",
    "size": 20,
    "ephemeral": false
  }
}
```

For a NodeSet spanning the participating hypervisors, select the logical network:

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

The scheduler picks an eligible subnet and hypervisor for each replica.
Optional hypervisor `site` labels group hosts into failure domains; without a
label, each hypervisor UUID is a domain. See [Logical networks](logical-networks.md).

Configure the load balancer's backend pool with on-premises private IPs.
Public frontend ingress is configured separately; the VPN gateway's floating IP
does not publish workloads. Security groups must permit frontend/backend traffic
and replies. The tunnel preserves private source addresses. Durable disks use
`ephemeral: false`; URL images must match `[openstack_cloud] image_origins`.
Provider flavors must match each requested vCPU/RAM configuration.

Add further subnets through the same API. New subnets in one cloud hypervisor
use a shared Neutron router under `auto`; other pairs use direct OpenVPN.
Transit and workload migration are outside this connectivity contract.

Remove workloads before deleting a subnet or the network. Cleanup is asynchronous.
Imported on-premises subnets survive detachment. Delete the cloud hypervisor
only after all dependent resources and cleanup records have gone.

## Manual setup through CLI

This sequence attaches an existing on-premises private subnet and one OpenStack
cloud to a logical network. Use an authenticated CLI context for the intended
Core installation, with infrastructure management permissions. Core, its agents
and images must have the compatible OpenStack/SDN drivers described above.
The commands configure an existing installation; image building is covered in
[Build](../app-developer-guide/build.md).

Fill the parameters below. Core and provider project IDs are distinct.
Use a workload image URL usable by both local hypervisors and OpenStack;
a provider-only Glance UUID cannot boot an on-premises VM. Required Nova flavors
must match the gateway and workload resources. Local workloads using
`--durable` also need a local storage pool with `ephemeral: false`.
Keep the generated UUIDs for subsequent show and delete commands.

```sh
PROJECT_ID='<CORE_PROJECT_UUID>'
ONPREM_HYPERVISOR_ID='<EXISTING_ONPREM_HYPERVISOR_UUID>'
ONPREM_SUBNET_ID='<EXISTING_PRIVATE_SUBNET_UUID>'
ONPREM_TRANSPORT_SUBNET_ID='<EXISTING_FLAT_TRANSPORT_SUBNET_UUID>'

OS_AUTH_URL='https://identity.example/v3'
OS_USERNAME='<OPENSTACK_USERNAME>'
OS_PROJECT_ID='<OPENSTACK_PROJECT_ID>'
OS_REGION='RegionOne'
OS_EXTERNAL_NETWORK_ID='<NEUTRON_EXTERNAL_NETWORK_UUID>'
OS_GATEWAY_IMAGE_ID='<GLANCE_GATEWAY_IMAGE_UUID>'
ONPREM_GATEWAY_IMAGE_URL='https://images.example/gateway.raw.zst'
WORKLOAD_IMAGE_URL='https://repo.exordos.com/<compatible-workload-image>'

new_uuid() { python3 -c 'import uuid; print(uuid.uuid4())'; }
CLOUD_HYPERVISOR_ID=$(new_uuid)
NETWORK_ID=$(new_uuid)
CLOUD_SUBNET_ID=$(new_uuid)
NODESET_ID=$(new_uuid)
WORK_DIR=$(mktemp -d)

exordos compute hypervisors list
exordos network subnets show "$ONPREM_SUBNET_ID"
```

Register the cloud. The defaults file contains no credentials; the password
is prompted without echo unless supplied through `OS_PASSWORD`.

```sh
cat > "$WORK_DIR/cloud-network-defaults.json" <<EOF
{
  "external_network": "$OS_EXTERNAL_NETWORK_ID",
  "gateway_image": "$OS_GATEWAY_IMAGE_ID",
  "role": "server"
}
EOF

exordos compute hypervisors add \
  --uuid "$CLOUD_HYPERVISOR_ID" --name openstack-a \
  --project-id "$PROJECT_ID" --site openstack-a \
  --kind openstack --auth-url "$OS_AUTH_URL" --region "$OS_REGION" \
  --auth-type v3password --username "$OS_USERNAME" \
  --os-project-id "$OS_PROJECT_ID" --user-domain-name Default \
  --network-defaults-file "$WORK_DIR/cloud-network-defaults.json"
```

Create the network, attach the existing subnet and allocate a cloud `/23`.
The on-premises CIDR is preserved. Repeat `--hypervisor` when the same local
subnet spans several compatible hosts in one fabric.

```sh
exordos network networks add \
  --uuid "$NETWORK_ID" --name application --project-id "$PROJECT_ID" \
  --cidr 10.80.0.0/16 --connectivity auto

cat > "$WORK_DIR/onprem-gateway.json" <<EOF
{
  "role": "client",
  "external_subnet": "$ONPREM_TRANSPORT_SUBNET_ID",
  "gateway_image": "$ONPREM_GATEWAY_IMAGE_URL"
}
EOF

exordos network subnets update "$ONPREM_SUBNET_ID" \
  --network "$NETWORK_ID" --hypervisor "$ONPREM_HYPERVISOR_ID" \
  --gateway-file "$WORK_DIR/onprem-gateway.json"

exordos network subnets add \
  --uuid "$CLOUD_SUBNET_ID" --name application-openstack-a \
  --project-id "$PROJECT_ID" --network "$NETWORK_ID" \
  --hypervisor "$CLOUD_HYPERVISOR_ID" --dns-server 1.1.1.1
```

Core creates the link and both gateways. The cloud server accepts the connection
on a floating IP; the on-premises client can be behind NAT.
No separate link creation or manual VPN configuration is needed.

```sh
exordos compute hypervisors show "$CLOUD_HYPERVISOR_ID" --output json
exordos network networks show "$NETWORK_ID" --output json
exordos network subnets show "$ONPREM_SUBNET_ID" --output json
exordos network subnets show "$CLOUD_SUBNET_ID" --output json
exordos network subnets list --filters "network=$NETWORK_ID"
```

Wait for the cloud hypervisor and both subnets to become ACTIVE. JSON subnet
responses contain `status` and `error`; network observations are under
`driver_state.subnets`. Resolve reported errors before creating workloads.
Renew DHCP in existing guests so they receive the peer routes.
The Core service address still needs its own attachment and return route
to the connected subnet.

Optionally start two replicas, one per failure domain:

```sh
exordos compute sets add \
  --uuid "$NODESET_ID" --name multicloud-smoke --project-id "$PROJECT_ID" \
  --network "$NETWORK_ID" --image "$WORKLOAD_IMAGE_URL" \
  --cores 1 --ram 1024 --root-disk 10 --durable --replicas 2 \
  --placement-kind anti-affinity --placement-scope site

exordos compute sets show "$NODESET_ID" --output json
exordos compute nodes list --filters "node_set=$NODESET_ID"
exordos network ports list
```

Use `--durable` for Cinder root disks. Both placement domains need sufficient
compute and durable storage capacity. With hard anti-affinity and two domains,
a third replica stays pending. Optional hypervisor `site` labels group multiple
physical hosts into one domain; otherwise each hypervisor UUID is a domain.

Find the replica IPs in Ports and test ICMP or an application TCP port in both
directions from the actual workloads. Security groups must permit the tested
traffic. Subnet ACTIVE describes connectivity readiness; it does not establish
application readiness or publish a public frontend.

OpenStack security groups require Neutron port security. Core rejects
`port_security: false` when `enforce_security_group` is true or a security group
is bound to the port. Core-backed providers keep these controls independent.
Manual local gateway agents must belong to a Node in the link project.
OpenStack secrets are read from the matching `OS_PASSWORD` or
`OS_APPLICATION_CREDENTIAL_SECRET` environment variable, or prompted without echo;
secret-valued command-line options are not accepted.

Cloud guests install the controller's SDK version by default. An SDK installed
from the official Git repository is installed from the same commit in guests.
To use another source, set `[cloud_agent] agent_package` in Core's
configuration to a reachable SDK wheel URL or package requirement. The setting
applies to all cloud providers and must reference the matching SDK build.
Package URLs cannot contain user information or query parameters; fragments must be SHA-256 integrity hashes.
