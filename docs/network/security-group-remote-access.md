# Remote security groups

`SecurityGroupRule.remote_group_id` matches Ports belonging to another SG or to
the same SG. Both groups must belong to one project. Membership is defined by
PortSecurityGroup bindings; the referenced group's rules are not inherited.

A rule matches members only in the protected Port's Network. The same SG can be
used across networks without allowing traffic between them. Remote groups are
ingress-only and cannot be combined with `remote_ip` on one rule.

```yaml
$core.network.catalog.security_group_rules:
  database:
    security_group: $core.network.catalog.security_groups.$db:uuid
    direction: ingress
    protocol: tcp
    port: 5432
    remote_group_id: $core.network.catalog.security_groups.$app:uuid
```

To allow communication within `db`, reference `$db` instead of `$app`.

## Dataplane

Core allocates one stable bit per remotely referenced `(Network, SecurityGroup)`
pair. Up to 16 such groups fit in each Network; other groups consume no bits.
A seventeenth allocation fails atomically. Assignments persist until SG or
Network deletion; adding groups does not renumber existing assignments.

Each sender's membership mask travels in `pkt_mark` between local OVS bridges
and VXLAN GBP between hosts. The receiving Port matches the relevant bit.
Membership changes update sender masks without distributing IP membership lists.
The low 16 mark bits are reserved for this policy; host firewall/routing rules
must preserve them. Anti-spoofing is controlled separately by `port_security`.

For default filtering and enforcement flags, see the
[API security rules](sdn-api-contract.md#security).

## Connection lifetime

Remote-group membership is checked when a new connection enters conntrack.
Removing a PortSecurityGroup binding updates the sender's identity and prevents
new connections permitted only by that membership. Existing tracked connections
continue until they close or expire; membership changes alone do not flush
conntrack on receiving hosts. If removing a binding also removes the sender's
own allow rules, rule-revocation semantics apply to those rules.

Deleting or changing an allow rule is different: the receiving Port flushes
sessions covered by the removed rule when it applies the change. The flush can
also interrupt connections allowed by another overlapping rule.
