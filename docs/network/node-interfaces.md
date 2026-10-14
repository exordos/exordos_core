# How a node says what network it is on

Node and NodeSet use `default_network` to request a primary Port and optional
floating IP. The [SDN API contract](sdn-api-contract.md#node-creation-and-attachment)
describes the request and placement rules.

```yaml
$core.compute.nodes:
  web:
    name: "web-1"
    cores: 2
    ram: 2048
    default_network:
      subnet: "$core.network.subnets.$private:uuid"
      floating_ip: true
    disk_spec:
      kind: "root_disk"
      size: 10
      image: "https://repo.exordos.com/exordos-base/1.1.0/exordos-base.raw.zst"
```

## Creation and observation

| Request | Primary Port |
| --- | --- |
| Field omitted | Automatic placement according to compatibility policy |
| `{}` | Inherited flat placement when compatibility selects it; otherwise no Port |
| `network` or `subnet` | Explicit private placement |
| `floating_ip: true` | Automatic FIP; implicit private placement always uses SDN |

Node applies the template only on creation. Read the result from
`default_network.ipv4`, `default_network.port` and the detailed `ports` list.
NodeSet applies its template to new replicas; existing replicas retain their
attachments. Replica addresses remain in `nodes[uuid].ipv4`.

The removed `networking` field is rejected. Additional NICs are separate Port
resources attached through `Port.node`; security and identity memberships use
PortSecurityGroup and PortIdentityGroup resources. Address resources manage FIPs.
These resources can be declared and updated independently.

## Legacy interfaces

Explicit `interfaces` declarations remain supported for compatibility and cannot
be combined with a `default_network` request. An interface names a subnet or an
existing Port, with an optional index. An empty `interfaces: []` declares no legs.
New manifests should use `default_network` and canonical resources.

Index 0 is primary for boot/image operations. An independently attached Port
gets the lowest free index when omitted. Detached Ports retain their allocated
IP/MAC; live and persistent VM NIC hotplug are supported. Removing the last
Port does not trigger another automatic Port.

Group lists and service switches inside `interfaces[].config` are rejected.
Port service switches are `dhcp`, `dns`, `network_identity`, and `port_security`.
Legacy interface configuration can carry the `simple` kind's public address
request. New primary FIP requests use `default_network.floating_ip`.

## Related

- [Private networks](private-network.md)
- [Security groups across a network](security-group-remote-access.md)
- [Realm connectivity SDN](realm-connectivity-sdn.md)
