# Core Developer Guide

## Export/Import Link Resolution

The element engine resolves import links to exported resources via
`ElementEngine.get_resource_by_export_link()`. This function matches an import
link against the element's registered exports and returns the corresponding
resource.

### Link Format

**Export full link** is constructed by the `Export.full_link` property:

```text
${element.name}.${link}
```

**Import link** is constructed at the call site:

```text
${element_prefix}.${import_link}
```

Both resolve to the same dotted path structure:

```text
$prefix.namespace.type.$resource_name
```

### Matching Algorithm

The function tries two strategies in order:

1. **Exact match** — the full export link string equals the import link string.
2. **Suffix match** — the export link without its first segment
    (`export_parts[1:]`) equals all of the import link parts. This handles the
    case where `${element.name}` duplicates the first `$`-prefixed segment of
    `link`.

### Examples

#### Simple direct lookup

A manifest exports a resource and the same manifest imports it. The element
prefixes and paths match exactly.

| Side | Link |
|------|------|
| Export full link | `$dbaas.$dbaas.types.postgres.instances.$cluster_pg` |
| Import link | `$dbaas.types.postgres.instances.$cluster_pg` |

- **Exact match**: no (`$dbaas.$dbaas.` != `$dbaas.`)
- **Suffix match**: `export_parts[1:]` = `$dbaas.types.postgres.instances.$cluster_pg` == import link → **match**

#### Cross-element import

Element `baz` imports from element `dbaas`. The element prefix differs, but the
path structure is identical.

| Side | Link |
|------|------|
| Export full link | `$dbaas.$dbaas.types.postgres.instances.$cluster_pg` |
| Import link | `$dbaas.types.postgres.instances.$cluster_pg` |

Same as above — suffix match resolves correctly.

#### Same element, same resource name, different paths

An element exports two resources with the same name in different namespaces.
The import must resolve to the correct one.

| Side | Link |
|------|------|
| Export 1 full link | `$dbaas.$dbaas.types.postgres.instances.$cluster_pg` |
| Export 2 full link | `$dbaas.$dbaas.types.volumes.instances.$cluster_pg` |
| Import link | `$dbaas.types.volumes.instances.$cluster_pg` |

- Export 1 suffix: `$dbaas.types.postgres.instances.$cluster_pg` — no match
- Export 2 suffix: `$dbaas.types.volumes.instances.$cluster_pg` — **match**

#### Cross-element with same path structure

Two different elements (`foo` and `bar`) export a resource with the same path
structure and resource name. Element `baz` imports from `bar`.

| Side | Link |
|------|------|
| Export foo full link | `$foo.$foo.types.postgres.versions.$cluster` |
| Export bar full link | `$bar.$bar.types.postgres.versions.$cluster` |
| Import link | `$bar.types.postgres.versions.$cluster` |

- Export foo suffix: `$foo.types.postgres.versions.$cluster` — no match
  (first segment `$foo` != `$bar`)
- Export bar suffix: `$bar.types.postgres.versions.$cluster` — **match**

#### Export link without element prefix duplication

When the export link does **not** start with the element prefix, `full_link`
has no duplication and the exact match succeeds.

| Side | Link |
|------|------|
| Export full link | `$test_export_node_2.$core.compute.nodes.$test_node` |
| Import link | `$test_export_node_2.$core.compute.nodes.$test_node` |

- **Exact match**: `$test_export_node_2.$core.compute.nodes.$test_node` ==
  `$test_export_node_2.$core.compute.nodes.$test_node` → **match**

#### Mismatched path structure

The import link has a different intermediate path than any export.

| Side | Link |
|------|------|
| Export full link | `$dbaas.$dbaas.types.postgres.instances.$cluster_pg` |
| Import link | `$stand.types.postgres.versions.$cluster_pg` |

- Exact match: no
- Suffix: `$dbaas.types.postgres.instances.$cluster_pg` !=
  `$stand.types.postgres.versions.$cluster_pg` → **raises `ValidateException`**

#### Nonexistent resource name

No export has a matching resource name.

| Side | Link |
|------|------|
| Export full link | `$dbaas.$dbaas.types.postgres.instances.$cluster_pg` |
| Import link | `$stand.types.postgres.instances.$nonexistent` |

- Exact match: no
- Suffix: `$dbaas.types.postgres.instances.$cluster_pg` !=
  `$stand.types.postgres.instances.$nonexistent` → **raises `ValidateException`**
