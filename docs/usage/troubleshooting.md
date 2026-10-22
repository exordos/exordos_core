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

To see which hypervisor a node was placed on, call the node's `details` action:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://<api>/v1/compute/nodes/<uuid>/actions/details
```

It answers `{"hypervisor": "<uuid>"}` with a string UUID, or `{"hypervisor": null}` while the
node hasn't been scheduled yet.

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

## An element waits forever on a secret

A `$core.secret.secrets` resource with neither a `value` nor a `default_value` has nothing to deliver.
It stays in `NEW`, and every resource that renders `:value` from it keeps its old target state and
logs:

```text
Target state is not available for resource <resource> by reason: 'value'
```

Check whether the secret has a value at all — the API never returns one, so ask the database:

```bash
psql exordos_core -c \
  "SELECT name, status, value IS NOT NULL AS has_value,
          default_value IS NOT NULL AS has_default
   FROM secret_secrets;"
```

Set the value and the next reconciliation cycle unblocks the element:

```bash
curl --request PUT --location 'http://<api>/v1/secret/secrets/<uuid>' \
--header 'Content-Type: application/json' \
--header 'Authorization: Bearer $TOKEN' \
--data-raw '{"value": "the-real-value"}'
```

See [Secrets](../secret/secrets.md) for how a manifest ships a default value instead.

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

## Updating Core from an unpublished local build

Build both the bootstrap image and the compressed raw image used by autonomous
updates:

```bash
exordos build --element core --output-dir output .
exordos deploy --element core --element-dir output --timeout 1800
```

The default build formats are `qcow2,zst`. If you override
`GEN_IMG_FORMAT_CORE`, include `zst`: autonomous updates write a raw image to
disk and cannot write a qcow2 container directly. The rendered Core manifest
uses `images.exordos_core_raw_zst`, a `urn:images:` reference from the generated
inventory. The repository resolver selects its download URL; passing a
`repository` template variable does not override this image reference.

Repository-free deploy serves the local build while it waits for Core to become
`ACTIVE`. It requires direct connectivity between the build host and the realm.
For a remote realm, use `--repository <configured-repository>` with a repository
reachable by that realm. Keep the image source available throughout the reboot
and download.

Before publishing a changed Core guest target, the running Core probes the
resolved image URL with a ranged GET and a five-second timeout. HTTP failures,
network failures, missing URLs and empty responses leave the previous guest
target unchanged. Reconciliation reports `Core update image is unavailable`;
restore the selected repository's image access and retry the deployment.
An unchanged image does not require a new probe.

This check runs in Core, so the currently running version must already include
it. It checks initial availability, not the complete download, image integrity,
boot prerequisites, or availability after the reboot.

### Regression checks

Run `tox -e build-contract` to exercise the CLI's real manifest renderer and
inventory writer without building a VM image. HTTP and target-generation tests
are in `exordos_core/tests/unit/compute/test_core_image_preflight.py`.

A complete upgrade acceptance run also requires a fresh isolated VM:

1. Save the installed versions, data-disk identity, and a persistent data marker.
2. Build an unpublished development version; verify its public image URL returns
   404 and its manifest URN matches the local inventory's `raw.zst` entry.
3. Deploy through `exordos`, keep the source available, and record the image GET
   from that source, the guest reboot, Core API recovery and `ACTIVE` status.
4. Verify the marker, data-disk identity and guest-agent identity survive.
5. On the patched running Core, test an unavailable replacement image. Verify
   the diagnostic, unchanged guest target, unchanged boot ID and a working API;
   restore image access and verify the supported deployment can continue.

## See also

- [Manifest reference](../em/manifest.md)
- [Core Developer Guide](../core-developer-guide/core-guide.md)
