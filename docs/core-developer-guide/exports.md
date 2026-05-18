Export/Import Guide

## Overview

Exports and imports allow elements to share resources. An element declares an
**export** to publish a resource for other elements, and another element
declares an **import** to consume that resource. The element engine resolves
import links to exported resources at manifest install/upgrade time.

## Data Models

### Export (`exordos_core/elements/dm/models.py:925`)

| Field     | Type           | Description                            |
|-----------|----------------|----------------------------------------|
| `element` | `Element` (FK) | The element that owns the export       |
| `name`    | `String`       | Export name, unique per element        |
| `kind`    | `Enum`         | Export kind; currently only `resource` |
| `link`    | `String`       | Dotted path to the exported resource   |

The `full_link` property returns `{element.link}.{link}` — the canonical
identifier used for matching imports.

### Import (`exordos_core/elements/dm/models.py:958`)

| Field            | Type             | Description                                         |
|------------------|------------------|-----------------------------------------------------|
| `element`        | `Element` (FK)   | The element that imports                            |
| `name`           | `String`         | Import name, unique per element                     |
| `from_element`   | `Element` (FK)   | The element that exports the resource               |
| `from_resource`  | `Resource` (FK)  | The specific exported resource                      |
| `kind`           | `Enum`           | Import kind; currently only `resource`              |

The `link` property returns `{element.link}.imports.${name}` — how the
imported resource is addressed inside the importing element.

### ImportedResource (`exordos_core/elements/dm/models.py:996`)

A lightweight proxy that delegates attribute access to the underlying
`Resource`. Used by the element engine to represent imported resources in
the namespace without duplicating the resource record.

## YAML Manifest Examples

### Exporting a resource

An element publishes a resource by declaring it under `exports`:

```yaml
# manifest-export.yaml
name: "test_export_node_1"
description: "Exports a compute node"
schema_version: 1
version: "0.0.1"
api_version: "v1"

requirements:
  core:
    from_version: "0.0.0"

resources:
  $core.compute.nodes:
    test_node:
      name: "shared-node-name"
      description: "Test node from manifest 1"
      cores: 1
      ram: 1024
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      disk_spec:
        kind: "root_disk"
        size: 10
        image: "https://repo.exordos.com/exordos-base/1.1.2/exordos-base.raw.zst"

exports:
  shared_node:
    link: "$core.compute.nodes.$test_node"
```

The export name `shared_node` is local to this element. The `link` field
points to a resource declared in the `resources` section.

### Importing a resource

Another element consumes the exported resource via `imports`:

```yaml
# manifest-import.yaml
name: "test_import_node"
description: "Imports a node from test_export_node_2"
schema_version: 1
version: "0.0.1"
api_version: "v1"

requirements:
  core:
    from_version: "0.0.0"

resources:
  $core.compute.nodes:
    test_node:
      name: "shared-node-name"
      description: $test_import_node.imports.$test_node:description
      cores: 1
      ram: 1024
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      disk_spec:
        kind: "root_disk"
        size: 10
        image: "https://repo.exordos.com/exordos-base/1.1.2/exordos-base.raw.zst"

imports:
  test_node:
    element: "$test_export_node_2"
    kind: "resource"
    link: "$core.compute.nodes.$test_node"
```

- `element`: which element to import from (by its `$name` link)
- `kind`: must be `"resource"` (the only kind supported)
- `link`: the resource path *within* the exporting element's resources

The importing element can then reference the imported resource's fields with
the `$element.link.imports.$name:field` syntax (see `description` above).

### The core element's exports

The `core` element itself exports several resources for other elements to use:

```yaml
# manifests/examples/core.element.yaml (excerpt)
exports:
  local_domain:
    link: "$core.dns.domains.$local_domain"
  var_core_ip_address:
    link: "$core.vs.variables.$core_ip_address"
  var_default_cores:
    link: "$core.vs.variables.$default_cores"
  var_default_ram:
    link: "$core.vs.variables.$default_ram"
  var_default_replicas:
    link: "$core.vs.variables.$default_replicas"
  profile_develop:
    link: "$core.vs.profiles.$develop"
  profile_small:
    link: "$core.vs.profiles.$small"
  profile_medium:
    link: "$core.vs.profiles.$medium"
  profile_large:
    link: "$core.vs.profiles.$large"
  profile_legacy:
    link: "$core.vs.profiles.$legacy"
```

## Link Resolution

### Export link format

```text
${element.name}.${link}
```

Example: `$dbaas.types.postgres.instances.$cluster_pg`

### Import link resolution

When `Manifest.apply_imports()` processes an import:

1. Look up the exporting element by its `$name` link in the element engine
2. Resolve the export link within that element via `get_resource_by_export_link()`
3. Match the full import link against the `_resource_exports` dictionary
4. Create an `Import` record and an `ImportedResource` proxy in the element engine

### Matching algorithm

The full import link is constructed as:

```text
{from_element.link}.{import.link}
```

This is compared against registered export full links. The match is exact —
the full link strings must be identical.

**Example with matching element prefixes:**

| Export full link                              | Import full link                                    | Match |
|-----------------------------------------------|-----------------------------------------------------|-------|
| `$dbaas.types.postgres.versions.$pg18`        | `$dbaas.$dbaas.types.postgres.versions.$pg18`       | Yes   |
| `$dbaas.types.postgres.instances.$cluster_pg` | `$dbaas.$dbaas.types.postgres.versions.$cluster_pg` | No    |
| `$foo.types.postgres.versions.$cluster`       | `$bar.$bar.types.postgres.versions.$cluster`        | No    |

### Invalid examples

**Missing `link` in export** (uses `no_link` instead):

```yaml
exports:
  example_node:
    no_link: "$core.compute.nodes.example_node"
```

This produces a schema validation error because `link` is required.

**Missing `element` in import:**

```yaml
imports:
  var_default_cores_invalid:
    kind: "resource"
    link: "$core.vs.variables.$default_cores"
```

This produces a schema validation error because `element` is required.

**Importing from an unknown element:**

```yaml
imports:
  test_node:
    element: "$test_export_node_unknown"
    kind: "resource"
    link: "$core.compute.nodes.$test_node"
```

Raises `exceptions.ValidateException` at install time because the element
does not exist in the engine.

**Importing a resource not in exports:**

```yaml
imports:
  test_node:
    element: "$test_export_node_2"
    kind: "resource"
    link: "$core.compute.nodes.$test_node_unknown"
```

Raises `exceptions.ValidateException("is not in export list")` at install
time.

## Lifecycle

### Install

`Manifest.install()` → `Manifest.apply_element()` → `apply_imports()` then
`apply_exports()`. Imports are processed before resources so that imported
values are available during resource value rendering.

### Upgrade

`Manifest.upgrade()` follows the same flow. Existing imports/exports are
updated in place; removed ones are deleted along with their
`ImportedResource` proxies.

### Uninstall

`Manifest.uninstall()` checks for dependent elements via
`_check_no_dependents()` before removing the element and all its
imports/exports.

## See also

- [Core Developer Guide](index.md) — architecture overview
- [Manifest reference](../em/manifest.md) — manifest YAML specification
