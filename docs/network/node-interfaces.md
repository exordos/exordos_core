# Node ports

Use `default_network` on Node or NodeSet to request one primary Port and optional
floating IP. The [API contract](sdn-api-contract.md#node-creation-and-attachment)
describes default placement and flat compatibility.

```yaml
$core.compute.nodes:
  web:
    name: web-1
    cores: 2
    ram: 2048
    default_network:
      network: $core.network.networks.$private:uuid
      auto_floating_ip: true
    disk_spec:
      kind: root_disk
      size: 10
      image: https://repo.exordos.com/exordos-base/1.1.2/exordos-base.raw.zst
```

Omit `default_network` for automatic placement. `{}` suppresses the Port unless
flat compatibility selects an existing project subnet. `auto_floating_ip: true`
without explicit placement always selects SDN. Optional `floating_network`
selects the external pool's Network and requires `auto_floating_ip=true`.

Node stores the template and creates its Port/FIP only on insert. Updates to the
template preserve existing attachments. Read `default_network.ipv4`, `.floating_ip`
and `.port` from the current Port/Address; the floating address is omitted when unbound. Query
`/v1/network/ports/?node=NODE_UUID` for all Ports and their status.

NodeSet applies its template to new replicas, each with its own Port and optional
FIP. Existing replicas keep their attachments. Read addresses from
`nodes[uuid].ipv4` and `.floating_ip`; manage replica networking through NodeSet.
Fixed IPs and multiple-Port templates are unsupported.

## Additional ports

An ordinary Node can attach independent Ports through `Port.node`. Omit `index`
for the lowest free slot; index 0 is primary for boot/image operations.
Detachment keeps allocated IP/MAC. Hotplug updates live and persistent libvirt
configuration. Removing the last Port does not create another.

SG memberships use PortSecurityGroup; FIPs use Address. Automatic Port/FIP
ownership follows Node/Port deletion. Independent resources have their own
lifecycle; see [ownership](sdn-api-contract.md#ownership-and-deletion).
