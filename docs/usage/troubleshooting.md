---
icon: lucide/wrench
title: Troubleshooting
---

This page collects common issues when developing and deploying elements on Exordos Core, and how to diagnose them.

## A resource is stuck in `IN_PROGRESS`

An element's resources go through the lifecycle `NEW → IN_PROGRESS → ACTIVE`. If a resource stays in
`IN_PROGRESS`, the platform has not been able to reconcile the declared state with reality yet.

Check the resource state:

```bash
exordos em resources list
exordos em resources show <uuid>
```

If the resource never leaves `IN_PROGRESS`, the compute node it targets may never have come up. Check the
node and hypervisor status:

```bash
exordos compute nodes list
exordos compute hypervisors list
```

See [Local Deployment](local_deployment.md) for hypervisor setup requirements.

## `$core.config.configs` fails instead of rendering

Both `project_id` and `body.kind` are required fields on a `Config` resource — neither has a default.
Omitting `body.kind` (for example `text`) makes the manifest fail with an `UnknownType: Unknown kind for
value: ...` error instead of producing an empty or partial file.

```yaml
$core.config.configs:
  my_config:
    project_id: "12345678-c625-4fee-81d5-f691897b8142"
    path: /etc/my_element_init.txt
    target:
      kind: node
      node: $core.compute.nodes.$my_node:uuid
    body:
      kind: text # required, no default
      content: |
        ...
```

## Values inside a config body are not substituted

Manifest string values are only interpolated in two cases:

- A value starting with `$` is treated as a full resource link, for example
  `"$core.vs.variables.$default_cores:value"`.
- A value starting with `f"` is treated as an inline template, where `{$element.type.$name:field}`
  placeholders are substituted inside a larger string.

Any other string — including a multi-line `content:` block — is passed through verbatim. If you forget the
`f"` prefix, the `{$...}` placeholders are **not** silently dropped: they stay in the rendered output as
literal text. If a delivered file ends up containing `{$core.secret.passwords.$my_password:value}` instead
of an actual password, this is almost always a missing `f"` prefix.

```yaml
body:
  kind: text
  content: |
    f"MY_PASS={$core.secret.passwords.$my_password:value}"
```

## Bootstrap script reads an empty config file

Content delivered through `$core.config.configs` is written to the target node as a plain file write: the
file is created (truncated) first, and the content is written afterwards — this is not an atomic
operation. A bootstrap script that starts before the content has arrived reads an empty file.

Always wait for the file to be **non-empty**, not just for it to exist:

```bash
while [ ! -s /etc/my_element_init.txt ]; do
    echo "Waiting for config..."
    sleep 2
done
source /etc/my_element_init.txt
```

If a script already checks `-s` and still reads an empty file, compare the content stored in the database
with what is on disk:

```bash
psql exordos_core -c "SELECT body FROM config_configs WHERE path = '/etc/my_element_init.txt';"
ssh ubuntu@<node-ip> "sudo cat /etc/my_element_init.txt"
```

If the database value is empty, the manifest itself has a rendering problem — see the previous section. If
the database has content but the file on disk doesn't, the node hasn't received it yet — wait, or re-run
the bootstrap script manually once it has.

## CLI acts as the wrong user after switching identity

The `exordos` CLI caches access and refresh tokens per **realm** in
`~/.exordos/exordos_tokens.json`, not per user. As long as a realm has a cached
token, the CLI reuses it and ignores the username you pass — so switching the
authenticated identity on the same realm (a different `settings set-context`, or
a `-u`/`--username` override) silently keeps acting as the previously cached user.
The symptom is a tenant that "sees everything" or an admin that unexpectedly gets
`403`: the visibility filter and permission checks reflect the cached identity,
not the one you meant to use.

There is no `logout` command. Clear the cache when you change identity:

```bash
rm ~/.exordos/exordos_tokens.json
```

The cache is refreshed automatically on the next command. This matters most when
testing multi-tenant behaviour, where admin and tenant runs alternate against one
realm.

## See also

- [Manifest reference](../em/manifest.md)
- [Core Developer Guide](../core-developer-guide/core-guide.md)
