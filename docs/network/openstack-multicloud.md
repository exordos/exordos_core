# OpenStack multicloud

Core can manage several OpenStack accounts through native Nova, Neutron and
Cinder APIs. Each `CloudConnection` selects one operator-managed `clouds.yaml`
entry and region. The controller performs provider API calls; guest agents do
not receive OpenStack credentials. Install Core and SDK with the `openstack`
extra and make `OS_CLIENT_CONFIG_FILE` available to the Core general service.
Protect that file with mode `0600`.

For an existing installation, follow [Connect on-premises to OpenStack](onprem-openstack.md).

## Resources

Create these resources in order through the authenticated user API:

1. `POST /v1/compute/cloud_connections/`: `project_id`, `name`, `cloud`,
   `driver: "openstack"`, `region`, `orch_endpoint`, `status_endpoint`,
   `agent_package` (an installable SDK package URL or pinned requirement).
2. `POST /v1/network/networks/`: the same `project_id`, and
   `driver_spec: {"driver": "cloud", "connection": "<connection UUID>",
   "external_network": "<Neutron external network UUID>"}`.
3. `POST /v1/network/subnets/`: `network`, IPv4 `cidr`, `dns_servers`, and
   `routers: [{"to": "0.0.0.0/0", "via": "<subnet gateway>"}]`.
4. `POST /v1/compute/hypervisors/`:
   `driver_spec: {"kind": "cloud", "connection": "<connection UUID>"}`.
5. Create a private link as described below, then ordinary compute nodes or
   NodeSets referencing the cloud subnet.

Only one pool may consume a connection's shared quotas. Connections and cloud
pool/network scope and agent bootstrap settings are immutable. Delete dependent nodes, pools, subnets and
networks before deleting a connection. Connection and link administration use
the existing compute hypervisor policy.

A cloud node uses a cloud-init capable image with compatible guest agents.
`disk_spec.image` accepts a Glance image ID/name or an Exordos HTTP(S) image URL.
The provider imports raw/qcow2, optionally gzip/zstd-compressed, into a private,
connection-owned Glance image and reuses the active import on retries. Incomplete
owned imports are replaced; foreign images are never adopted by URL.
The operator configures `[openstack_cloud] image_origins` (default
`https://repo.exordos.com`); redirects must remain within this allowlist. The
provider limits compressed and unpacked bytes (`image_import_max_bytes`, 64 GiB),
download/decompression time and Glance wait (`image_import_timeout`, 900 seconds),
and temporary filesystem reserve (`image_import_free_bytes`, 1 GiB). The host
needs enough temporary disk for the unpacked image. A matching Nova flavor must
exist for the requested vCPU/RAM. Root disks use Cinder and require
`disk_spec.ephemeral: false`; additional durable volumes use the ordinary
volume contract. A NodeSet using a cloud subnet defaults unspecified replica
disks to durable Cinder storage, preserving explicitly requested values. Nova-local (ephemeral) disk placement is not implemented.
The selected volume type defaults to `__DEFAULT__`. Provider IPAM assigns
automatic addresses and MACs; Core records the observed Neutron binding.

## Private routed connectivity

`POST /v1/compute/cloud_network_links/` creates a symmetric full mesh:

```json
{
  "project_id": "<Core project UUID>",
  "name": "application-sites",
  "transport": {"driver": "openvpn", "protocol": "tcp", "port": 443},
  "endpoints": [
    {
      "subnet": "<Core cloud subnet A UUID>",
      "external_network": "<A Neutron external network UUID>",
      "gateway_image": "<A Ubuntu Glance image UUID>"
    },
    {
      "subnet": "<Core cloud subnet B UUID>",
      "external_network": "<B Neutron external network UUID>",
      "gateway_image": "<B Ubuntu Glance image UUID>"
    }
  ],
  "mtu": 1380
}
```

Each endpoint derives its provider connection from `Subnet.Network`. The link
requires at least two distinct IPv4 subnets with non-overlapping CIDRs.
There is no fixed endpoint-count limit in the common API. A subnet belongs
to one link until cleanup completes. Add or remove endpoints with a resource
`PUT`; placement settings, transport and MTU are immutable. Changes converge
asynchronously: wait for `ACTIVE` before treating the new topology as ready.

Each cloud endpoint gets one dedicated gateway VM, Neutron port and security
group. An explicit `client` endpoint uses outbound access without a floating IP;
`auto` and `server` endpoints get a floating IP. Set endpoint `role` to `server`
to accept connections, `client` to initiate them, or omit it for `auto`.
Explicit roles override UUID order. Two `auto` endpoints use the smaller UUID
as server. A pair of two explicit servers or two explicit clients is rejected:
every pair in a full mesh needs one accepting side and one initiating side.
Roles are immutable placement settings; changing one requires removing and
reattaching the endpoint. One server listener accepts incoming peers.
Every pair has exactly one connection; traffic does
not transit Core or a third site. OpenVPN uses routed TUN, TCP/443 and
`tls-crypt-v2` by default. UDP may be selected explicitly. There is no SNAT or
L2 extension. TCP/443 does not guarantee passage through provider DPI.

Core reserves the listening gateway addresses before generating complete cloud-init
configurations. The OpenStack driver checks the actual gzip-compressed, base64
bootstrap against Nova's 65,535-character user-data limit before server creation;
oversized payloads fail with their encoded size and the provider limit.
Nova receives compressed user-data; OpenVPN and forwarding
start before the SDK agent and do not call Core APIs. Package repositories
must be reachable during bootstrap. Subsequent changes use the ordinary SDK
agent contract, kind `cloud_net_link`. Install OpenVPN **2.6 or later** on the
Core general-service host for `tls-crypt-v2` key generation; the Core image
installer includes this dependency. No VPN interface runs on Core by default.

Neutron DHCP host routes direct peer prefixes through each subnet's gateway.
Existing guests must renew DHCP after membership changes. Conflicting routes
to overlapping peer prefixes are rejected. Allowed address pairs and gateway
firewall rules preserve source addresses while restricting forwarded prefixes.
New tunnel connections cannot access gateway host services. Established replies
to gateway-originated requests are allowed; managed gateways use their private
subnet IP as the source for those requests.
Core manages a private CA, distinct client/server certificates and client
`tls-crypt-v2` keys. CA signing keys stay on Core. Certificates are renewed
within 30 days of expiry; CA renewal preserves the signing key so independently
updated agents retain mutual trust.

A Core SDN endpoint uses the same link API:

```json
{
  "subnet": "<Core ovs_evpn subnet UUID>",
  "role": "client",
  "external_subnet": "<placeable Core flat_bridge subnet UUID>",
  "gateway_image": "https://repo.example/cloud-link-gateway.raw.zst",
  "gateway_cores": 1,
  "gateway_ram": 1024,
  "gateway_disk": 8
}
```

Core creates an ordinary managed Node, its private primary port and flat
transport port, Config and Service for a separate `cloud_net_link` agent.
The node image must contain compatible Core guest/render/service agents;
OpenVPN 2.6+, iptables and iproute2 must be installed or available from apt.
The gateway's private port disables anti-spoofing because it forwards remote
prefixes. Core publishes peer routes through the subnet's routers; the matching
SDN SDK advertises those prefixes behind the forwarding port in the L3 fabric.
A DHCP route alone cannot attach a gateway to a fabric that proxies ARP.
The external subnet must belong to the project and be placeable (`next_server`
unset), with a reachable default gateway. A `client` can use a private transport
address behind NAT; it needs outgoing access to the server's transport port.
A `server` or `auto` endpoint's flat-port address must be reachable from peers.
Node/port changes go through the link;
direct tenant mutation is rejected. Missing managed ports are recreated with
reserved addresses. Deletion uses the ordinary Core builders for teardown.

Use the matching SeedOS initrd: it preserves RFC3442 metadata host routes during
bootstrap and gives non-metadata NICs priority for default routes independent
of DHCP renewal order. An existing gateway may instead be an endpoint with
`{"subnet": "<UUID>", "agent": "<SDK agent UUID>", "endpoint": "<public IPv4>"}`.
Its agent must advertise `cloud_net_link`, run with root privileges and have
OpenVPN 2.6+, iptables, iproute2 and systemd. One physical gateway supports one
endpoint. The operator provides local routes to and from its subnet, including
the attachment to EVPN for this pre-existing gateway mode.
Core API placement is independent: a private API needs its own attachment and
return route to a connected subnet.

An outbound-only existing gateway uses
`{"subnet": "<UUID>", "agent": "<SDK agent UUID>", "role": "client"}`;
no public `endpoint` is needed. For on-premises workloads behind NAT and cloud
load balancers, use `client` on-premises and `server` in OpenStack. Connections
initiated by the load balancer travel inside the established bidirectional
tunnel; application security groups and return routes must permit them.

`ACTIVE` requires active Nova gateways and healthy direct OpenVPN connections
reported for the current target hash and gateway incarnation. This is tunnel
health, not a substitute for workload-to-workload acceptance tests. Saved
configuration survives agent and Core outages. A lost cloud gateway is
recreated through provider APIs. To explicitly replace an unreachable gateway:

```text
POST /v1/compute/cloud_network_links/<link UUID>/actions/recover_gateway/invoke
{"subnet": "<Core subnet UUID>"}
```

This requires link update permission and does not contact the failed agent.
The gateway port and floating IP are retained; its Nova server is replaced.
Recovery and membership updates require a working controller/provider API;
the transport bootstrap itself does not require guest access to Core.

Deletion retains a `DELETING` record until owned targets, routes, gateways,
ports, floating IPs and security groups are removed. Removing one cloud endpoint
immediately fences its Nova gateway, then revokes its configuration on
surviving peers. Other resources remain owned until survivors acknowledge.
Revocation on an unavailable local endpoint remains asynchronous. The subnet/connection
binding stays until cleanup finishes. Failed cleanup is retried. Forwarding
settings on a local gateway are restored after the last managed link is removed.

See [CloudNetworkLink architecture](cloud-net-link-design.md) for the bootstrap
sequence and operational limits.

## Core hosted in OpenStack

Core may run in a provider VM with a Neutron interface in a link's control-plane
subnet. Its private Orchestrator and Status addresses are ordinary guest IPs;
the same CloudNetworkLink connects that subnet to remote PaaS subnets. Both
endpoints use the cloud driver. No Core-local VPN or API relay is required.

Bootstrap the Core VM and its database independently of the resources it will
manage. Give the controller access to the provider's Keystone catalog endpoints
through the provider's public or management network before creating a link.
This access must survive loss of the private link: it is needed to provision
or replace the remote gateway. Only the controller holds provider credentials.

Configure CloudConnection and PaaS agent endpoints with Core's private service
address. Allow the required agent ports from the connected private prefixes in
the Core VM's security group. The controller reserves and bootstraps both
gateways through provider APIs; their saved OpenVPN configuration establishes
connectivity before remote agents can contact Core. An existing Core VM must
renew DHCP to receive the link's return routes, just like other existing guests.

Once established, the data path and PostgreSQL leader election do not require
Core to be available. Reconciliation and gateway replacement resume when Core
returns. This does not provide HA for the Core VM, its database or link gateways.

## Guest identity and recovery

Cloud-init writes an explicit Core node identity and a mode `0600` encryption
key, installs the SDK and enables `exordos-agent.service`. The provider's DMI
UUID and metadata API remain intact. The guest agent uses the private
Orchestrator and Status endpoints with the existing encrypted agent protocol.
`agent_package` and its dependencies must be reachable during bootstrap.
On images with a preinstalled Exordos agent, cloud-init queues startup after
`cloud-config.service`, so baked image services
waiting for agent-delivered configuration cannot hold it behind cloud-final's
multi-user target. OpenVPN gateways queue transport startup before the agent
on this early path as well as on the cloud-final fallback. Plain Ubuntu images
use cloud-final after their venv/native packages have been installed.
The systemd service retries a failed SDK installation and skips installation
after the agent executable has been installed successfully.

Extension drivers registered as `gcl_sdk_cloud_driver` must subclass
`OpenStackDriver` and retain its OpenStack SDK and ownership contract. The
loader rejects incompatible APIs before provisioning starts.

Provider objects carry an `exordos:<connection>:<kind>:<Core UUID>` marker;
retries recover existing objects instead of adopting arbitrary objects by
name. Credentials must be isolated per provider project: principals allowed to
edit these markers share that project's trust boundary. Core reconciliation
serializes network/link provider effects and protects connection deletion
against concurrent dependent creation.

## Current scope

The generic OpenStack driver supports native compute lifecycle, Cinder
volumes, private networks/subnets/ports, security groups and routed OpenVPN
links, URL image import and managed Core SDN gateways. It does not change an
existing node's image in place or provide L2/VXLAN federation or overlapping
CIDRs inside one link. Image changes require explicit node replacement.
Provider extensions for Selectel and VK Cloud/Sprut are unvalidated.
Running a particular PaaS/DBaaS service on these nodes
requires that service's own deployment and acceptance checks.

## SDK and agent requirements

Core and gateway images require an SDK containing `gcl_sdk.cloud.openvpn`
and `OpenVPNCapabilityDriver`. PaaS images require service agents that use
the configured Core node identity rather than Nova's DMI UUID.

For a Core IAM API without the `default` client alias, configure
`core_client_uuid` in both the DBaaS CoreInfraBuilder and UAgent settings.
