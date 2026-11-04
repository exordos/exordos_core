# Core multicloud

A Core installation can consume another Core installation as a cloud provider.
The upper Core owns workload intent and application configuration. The remote
Core owns VM, disk and network provisioning on its exported native hypervisors.
Both control planes use their normal project-scoped APIs; no database access
between installations is required.

## Requirements

Install matching Core and SDK versions with the Core provider on both control
planes. The remote installation must export native `libvirt` or
`exordos_local_hyper` hypervisors with OVS enabled. Their pools and resources
must belong to the remote project supplied in the connection. Use a dedicated
project and credentials with permissions for compute, networks, ports, security
groups and configs. The upper operator must be permitted to register a cloud
hypervisor.

The guest image must contain a compatible universal agent at
`/usr/bin/exordos-universal-agent` and support systemd and cloud-init. A separate
agent, identity, key and state directory under
`/var/lib/exordos/core-provider/<connection>` connect the workload to the upper
Core. The remote Core's guest agent continues managing the underlying VM.
The upper Orch and Status API endpoints must be reachable from these guests.

Use HTTPS for the remote User API across untrusted networks. HTTP URLs are
accepted for trusted management networks. Protect the connection file because
it contains provider credentials; credentials and executable bootstrap content
are omitted from tenant API responses and sensitive request-body logging.

The provider bootstrap Config is managed through its Node. Direct edits return
HTTP 409; separate deletion also returns 409 while the owning Node exists.
Deleting the Node removes its bootstrap. Config delivery can update its status,
and orphaned bootstrap Configs can be collected after their Node is gone.

## Register the provider

Create a mode `0600` JSON file, replacing the example values:

```json
{
  "kind": "core",
  "api_url": "https://remote-core.example.org",
  "project_id": "11111111-1111-4111-8111-111111111111",
  "hypervisors": ["22222222-2222-4222-8222-222222222222"],
  "auth": {"username": "provider", "password": "REPLACE"}
}
```

The authentication object also accepts `client_uuid`, `client_id` and
`client_secret` for installations with a custom IAM client.

```bash
exordos compute hypervisors add --name remote-core --kind core \
  --driver-spec-file core-provider.json
```

The equivalent request is `POST /v1/compute/hypervisors/` with the JSON object
nested under `driver_spec`. The connection exposes a regular cloud pool to the
upper scheduler. Its remote API URL, project and exported hypervisors form an
immutable scope; register a separate connection to change that scope.

Create a [logical network](logical-networks.md) and a subnet whose `hypervisors`
contains the new upper hypervisor UUID. Nodes and NodeSets placed there create
VMs through the remote Core. Provider UUIDs and ownership markers make retries
idempotent and keep unrelated remote resources outside cleanup.

## Private connectivity

A logical network can span native, OpenStack and remote Core subnets. To use a
managed gateway on the Core site, configure `gateway` on that subnet, or
`network_defaults` on the connection:

```json
{
  "external_subnet": "33333333-3333-4333-8333-333333333333",
  "gateway_image": "https://images.example.org/exordos.raw.zst",
  "gateway_cores": 1,
  "gateway_ram": 1024,
  "gateway_disk": 8,
  "role": "server"
}
```

`external_subnet` is an operator-provided transport subnet in the remote
project. Its addresses must be reachable by peer gateways. The provider creates
a gateway VM with private and transport NICs, starts OpenVPN, and installs routes
through its reserved private address. SDN transport networks receive a dedicated
VPN security group; flat transport networks rely on the operator's perimeter
firewall. Gateway recovery creates a fresh VM while keeping reserved ports and
addresses. Workload security groups are projected through the remote API.
They remain enforced when `port_security=false` disables MAC/IP anti-spoofing;
the two controls are independent.

## Resource lifecycle

VM creation bundles root and data disks. CPU, memory and attached-disk growth
are supported. A disk is reported pending until the remote VM has created it;
remote readiness and upper guest-agent convergence are both required for an
active workload. Capacity is aggregated by disk tier and persistence class;
the remote scheduler selects physical pools and remains authoritative for
placement, free space and quotas.

Standalone unattached disks, disk detach or migration, changing disk policy,
reimaging an existing VM and explicit power operations are rejected. Replace a
VM to change its image. An outage of the remote API prevents reconciliation;
running guests and established private connectivity remain on the data plane.
Deleting an upper workload removes its owned remote VM, disks and bootstrap.
Deleting a logical network waits for remote network and gateway cleanup.
