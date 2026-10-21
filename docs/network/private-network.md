# Private networks and floating IPs

Private networks use L3 EVPN/VXLAN (`ovs_evpn`). Networks are isolated even when
their CIDRs overlap. A VM can request its project's default network without
creating a Network or Port first; see [Node ports](node-interfaces.md).

## Prepare hypervisors

Use matching Core/SDK/connector builds. Once per participating hypervisor:

```bash
sudo "$(command -v exordos)" compute hypervisors init --sdn \
  --sdn-connector /path/to/evpn_connector --add
```

For an existing pool, use `--sdn-pool POOL_UUID` and, when a dedicated SDN agent
is needed, `--sdn-project-id PROJECT_UUID` instead of `--add`.
The command installs OVS, gobgpd, the connector and agent, maps the pool to its
hypervisor identity and enables OVS tap wiring. Existing flat guests keep their
network. The agent creates per-network services as guests appear.

Allow the required host/RR connectivity and prepare the external uplink as
explained in [deployment](realm-connectivity-sdn.md). Creating a pool object alone
does not configure physical routing.

## Create guests

```bash
PROJECT=PROJECT_UUID
IMAGE=https://repo.exordos.com/exordos-base/1.1.2/exordos-base.raw.zst

exordos compute cn add -n vm-a -p "$PROJECT" -c 1 -r 1024 -d 10 -i "$IMAGE"
exordos compute cn add -n vm-b -p "$PROJECT" -c 1 -r 1024 -d 10 -i "$IMAGE"
```

Automatic SDN placement lazily creates the project's `10.128.0.0/20` default
network. On upgrades, flat compatibility may select an existing project subnet;
[placement rules](sdn-api-contract.md#node-creation-and-attachment) describe the
configuration switch and explicit empty template.

For an explicit private network:

```bash
exordos network networks add -n private -p "$PROJECT"
exordos network subnets add -n private -p "$PROJECT" -N private -c 10.102.0.0/24
exordos compute cn add -n vm-c -p "$PROJECT" -c 1 -r 1024 -d 10 -i "$IMAGE" \
  --network private
```

DHCP Subnets default gateway and DNS to their first host address. Overlay VMs
netboot through the hypervisor's DHCP/HTTP proxy. Use the x86_64 iPXE ROM from
`exordos_seed` for q35 guests; affected 32-bit ROMs stall with high PCI BARs.
Guest names and reverse IPv4 lookups resolve across hosts inside the Network
under the configured DNS suffix (`internal` by default). The name is the Port's
name, or its Node's name when omitted, and must be one valid DNS label. Ports
with `dns=false` or no attached Node are excluded. Renaming or removing a Port
updates the Network's directory on its serving hosts.

## Request a floating IP

An operator must prepare a reachable external pool, for example:

```yaml
$core.network.subnets:
  floating:
    network: $core.network.networks.$external:uuid
    cidr: 192.168.1.0/24
    ip_range: 192.168.1.50-192.168.1.99
    dhcp: false
    placeable: false
    floating_pool: true
    default_floating_pool: true
    shared_pool: true
```

Replace the range with addresses reserved for the installation. The pool requires
operator permission; its Network must be declared separately. Then request a
FIP while creating the VM:

```bash
exordos compute cn add -n public-vm -p "$PROJECT" -c 1 -r 1024 -d 10 -i "$IMAGE" \
  --auto-floating-ip
```

Use `--floating-network NAME_OR_UUID` to choose a pool Network explicitly.
A FIP is symmetric 1:1 NAT on the serving hypervisor. The guest retains its
private IP; outbound traffic uses its FIP. The external network must return
traffic to that hypervisor. Inspect `Node.default_network.floating_ip` and the
Address status to distinguish allocation from dataplane application.

## Apply security policy

```bash
exordos network sg add -n web -p "$PROJECT"
exordos network sg rule-add web ingress:tcp:22
exordos network sg rule-add web ingress:icmp
exordos network sg rule-add web egress:any
exordos network ports list --filters node=NODE_UUID
exordos network sg bind web PORT_UUID
```

Bound SGs combine allow rules and deny unmatched traffic. Without bindings,
Ports permit traffic unless `enforce_security_group=true`. Anti-spoofing remains
independent. [Remote groups](security-group-remote-access.md) select peers by SG
membership within the same Network.

The [complete manifest](../../exordos/manifests/examples/sdn-networking.yaml)
shows independent network, Port, FIP and policy resources. For access to an
installation HTTP service, use [ProxyService](paas-networking-guide.md).
