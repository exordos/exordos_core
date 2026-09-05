---
icon: lucide/file-text
---
# Manifest Guide

## Introduction

A **manifest** is a YAML file that declaratively describes everything an element needs to run on an Exordos
Core platform: infrastructure (virtual machines, node sets), services (systemd units), configuration files,
DNS records, load balancers, secrets (certificates, passwords), IAM entities, and platform variables.

A manifest is the blueprint; an **element** is the running instance created from it. The relationship is
similar to source code and a running process: the manifest is the code, the element is the process. Once
installed, the platform continuously reconciles the element's declared state against the actual state of
the infrastructure.

### Project, element, manifest

An Exordos **project** is your application's source tree. It contains an `exordos/exordos.yaml` file that
describes how to build one or more **elements**. Each element is built from a manifest and a set of images
and artifacts:

```text
project/
├── exordos/
│   └── exordos.yaml          # build config: images, artifacts, elements
├── manifests/
│   └── my-app.yaml           # manifest for the "my-app" element
└── src/
    └── ...                   # application code
```

One project can produce multiple elements, each with its own manifest. In this guide we focus on the
simple case: one project, one element, one manifest.

---

## Manifest structure

A manifest can be broken into several sections:

| Section | Purpose |
|---|---|
| **Metadata** | General information about the manifest: name, description, version, etc. |
| **Requirements** | Dependencies on other elements. For example, `dbaas >= 2.1.0`. |
| **Resources** | The resources the element needs to function: nodes, services, configs, DNS records, etc. |
| **Imports** | Resources imported from other elements. For example, a shared database connection. |
| **Exports** | Resources published by this element for other elements to import. |

### Minimal example

The following manifest defines a single element with one compute node, one systemd service, one import
(default CPU cores from the `core` element), and one export (the service itself):

```yaml
name: "my_app"
description: "My web application"
schema_version: 1
version: "1.0.0"
api_version: "v1"

requirements:
  core:
    from_version: "0.0.0"

resources:
  $core.compute.nodes:
    app_node:
      name: "my-app-node"
      cores: "$my_app.imports.$default_cores:value"
      ram: 1024
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      disk_spec:
        kind: "root_disk"
        size: 10
        image: "{{ base_image_url | default('https://repo.exordos.com/exordos-base/1.1.0/exordos-base.raw.zst') }}"

  $core.em.services:
    app_service:
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      name: "my-app"
      path: "/usr/bin/python3 -m http.server 8080"
      user: "ubuntu"
      group: "ubuntu"
      target:
        kind: "node"
        node: "$core.compute.nodes.$app_node:uuid"
      service_type:
        kind: "simple"
        count: 1
      target_status: "enabled"

imports:
  default_cores:
    element: "$core"
    kind: "resource"
    link: "$core.vs.variables.$default_cores"

exports:
  my_service:
    link: "$my_app.em.services.$app_service"
```

The rest of this guide walks through each section in detail.

---

## Metadata

The manifest begins with metadata fields:

```yaml
name: "core"
description: "Exordos Core element"
schema_version: 1
version: "1.0.0"
api_version: "v1"
```

| Field | Type | Description |
|---|---|---|
| `name` | string | Name of the element. Must be unique within a realm. |
| `description` | string | Human-readable description. |
| `schema_version` | integer | Manifest schema version. Currently `1`. |
| `version` | string | Element version. |
| `api_version` | string | API version the manifest targets. Currently `v1`. |
| `uuid` | uuid | Optional. A fixed UUID for the element. Auto-generated if omitted. |

The `name` and `version` together form the element's identity. The platform derives a deterministic UUID
from `{name}-{version}` when `uuid` is not specified.

---

## Requirements

The `requirements` section declares dependencies on other elements. Each entry is keyed by the element
name and may specify version bounds:

```yaml
requirements:
  core:
    from_version: "1.0.0"
  dbaas:
    from_version: "2.1.0"
    to_version: "3.0.0"
```

| Field | Type | Description |
|---|---|---|
| `from_version` | string | Minimum required version (inclusive). If omitted, no lower bound. |
| `to_version` | string | Maximum allowed version (exclusive). If omitted, no upper bound. |

Version constraints are checked at install time. If a required element is not yet installed but a
matching version is available in a registered repository, it is installed automatically as part of the
dependency closure. Installation fails only when no candidate satisfies the constraint or an installed
version conflicts with it.

A requirement of `from_version: "0.0.0"` with no `to_version` means "any version of this element".

---

## Resources

### What is a resource?

A **resource** is an abstraction over any infrastructure or platform entity the element needs: a virtual
machine, a database cluster, a configuration file, a DNS record, a load balancer, a secret, an IAM user —
all are resources with different types.

The `resources` section is the main part of the manifest. Each resource has a **kind** (the top-level key
under `resources`) and a **name** (the key within that kind):

```yaml
resources:
  $core.vs.profiles:
    develop:
      name: "develop"
      profile_type: "GLOBAL"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      description: "The develop profile"
```

Here `$core.vs.profiles` is the resource kind and `develop` is the resource name (key). Resources with the
same name but different kinds are allowed.

### Resource kind

The kind is a dotted path prefixed with `$`. It identifies which element provides the resource type and
which API collection is used to create it. For example, `$core.compute.nodes` means the `core` element
provides the `compute.nodes` resource type, and the platform will create the resource via the
`/v1/compute/nodes/` API endpoint.

The `$` prefix signals that the kind will be resolved during manifest processing — it is not a literal
string but a reference to the providing element's namespace.

### Resource fields

The fields of a specific resource are defined by its kind. In the example above, a profile resource has
`name`, `profile_type`, `project_id`, and `description` fields. These fields form the object that is sent
to the platform API to create the resource.

For a complete reference of all resource kinds and their fields, see the
[Resource Reference](resources.md).

### Nested resources

Some resource kinds support nested resources, addressed by extending the kind path with `.$name`:

```yaml
resources:
  $core.dns.domains:
    local_domain:
      name: "local.example.com"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"

  $core.dns.domains.$local_domain.records:
    core_record:
      domain: "$core.dns.domains.$local_domain:uuid"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      type: "A"
      record:
        kind: "A"
        name: "core"
        address: "10.20.0.2"
```

Here `$core.dns.domains.$local_domain.records` means "records within the `local_domain` domain resource".
The `$local_domain` segment is a reference to the parent resource by its key name.

---

## Imports

If an element depends on resources from other elements — for example, a shared database, a default
variable, or an exported node — those resources must be **imported**. The `imports` section declares which
resources to pull from which elements.

```yaml
imports:
  default_cores:
    element: "$core"
    kind: "resource"
    link: "$core.vs.variables.$default_cores"
```

| Field | Type | Description |
|---|---|---|
| `element` | string | The exporting element, referenced by its `$name` link. |
| `kind` | string | Import kind. Currently only `"resource"` is supported. Required by manifest validation. |
| `link` | string | Dotted path to the resource within the exporting element. |

The import name (the key, e.g. `default_cores`) is local to the importing element. Once imported, the
resource can be referenced in the `resources` section using the
`$<element_name>.imports.$<import_name>:field` syntax.

### Example: importing a node

Element `test_export_node_1` exports a compute node:

```yaml
# test_export_node_1.yaml
name: "test_export_node_1"
# ...
resources:
  $core.compute.nodes:
    test_node:
      name: "shared-node-name"
      # ...

exports:
  shared_node:
    link: "$core.compute.nodes.$test_node"
```

Element `test_import_node` imports that node and uses its fields in a config:

```yaml
# test_import_node.yaml
name: "test_import_node"
# ...

requirements:
  core:
    from_version: "0.0.0"
  test_export_node_1:
    from_version: "0.0.0"

resources:
  $core.compute.nodes:
    dependent_node:
      name: "dependent-node"
      # ...

  $core.config.configs:
    test_config:
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      path: /home/ubuntu/test_cfg.txt
      target:
        kind: node
        node: $core.compute.nodes.$dependent_node:uuid
      body:
        kind: text
        content: |
          f"
          description={$test_import_node.imports.$shared_node:description}
          "

imports:
  shared_node:
    element: "$test_export_node_1"
    kind: "resource"
    link: "$core.compute.nodes.$test_node"
```

### Import resolution

Imports are processed **before** resources during manifest installation, so imported values are available
when resource fields are rendered. The platform:

1. Looks up the exporting element by its `$name` link.
2. Resolves the export link within that element.
3. Creates an `Import` record and a proxy resource in the element engine.

If the exporting element is not installed, or the specified resource is not in its exports, installation
fails with a validation error.

---

## Exports

If an element's resources should be usable by other elements, they must be **exported**. The `exports`
section publishes resources under local names:

```yaml
exports:
  my_service:
    link: "$core_service_example.em.services.$example_service"
    kind: "resource"
  my_node:
    link: "$core_service_example.em.services.$example_node"
```

| Field | Type | Description |
|---|---|---|
| `link` | string | Dotted path to the resource being exported. **Required.** |
| `kind` | string | Export kind. Currently only `"resource"` is supported. May be omitted. |

The export name (the key, e.g. `my_service`) is local to the exporting element. Other elements reference
the export via their `imports` section, using the exporting element's name and the export's link.

### Example: the core element's exports

The `core` element exports several resources for other elements to use — default variables, profiles, and
the local DNS domain:

```yaml
exports:
  local_domain:
    link: "$core.dns.domains.$local_domain"
  var_default_cores:
    link: "$core.vs.variables.$default_cores"
  var_default_ram:
    link: "$core.vs.variables.$default_ram"
  profile_develop:
    link: "$core.vs.profiles.$develop"
```

Another element can then import `var_default_cores` to get the platform's default CPU core count, as
shown in the [minimal example](#minimal-example) above.

### Uninstall protection

An element cannot be uninstalled while other elements declare it in their `requirements`. The platform
checks `requirements` of installed elements before removing an element and its resources. Note that
imports alone do not protect the provider from uninstallation — if an importing manifest omits a
corresponding `requirements` entry, the provider can still be removed, leaving the importer with a
dangling resource reference.

---

## Manifest templates

A manifest file can be a Jinja2 template. During `exordos build`, the template is rendered with build-time
variables before the manifest is included in the element artifacts.

```yaml
image: "{{ base_image_url | default('https://repo.exordos.com/exordos-base/1.1.0/exordos-base.raw.zst') }}"
```

The following variables are available in manifest templates:

| Variable | Description |
|---|---|
| `{{ version }}` | Version of the element being built |
| `{{ name }}` | Name of the element |
| `{{ images }}` | Mapping of image names to their URN references, built for this element |
| `{{ manifests }}` | List of manifest files |
| `{{ artifacts }}` | Mapping of artifact names to their URN references |

Additional variables can be passed with `exordos build --manifest-var key=value`.

Template rendering happens at **build time**. The output is a static YAML file stored in the element
artifacts. At install time, the platform processes the rendered manifest — resolving `$` links and `f"..."`
interpolations (see [Links](#links)).

---

## Links

Links are the mechanism for referencing one resource from another, both within the same element and across
elements. A link is a string that the platform resolves at install/reconciliation time.

### Link syntax

There are two forms:

**Direct reference** — a string starting with `$`:

```yaml
node: "$my_app.compute.nodes.$app_node:uuid"
```

The format is:

```text
$<element>.<resource_kind>.$<resource_name>:<parameter>
```

- `<element>` — the element name (e.g. `my_app`, `core`).
- `<resource_kind>` — the dotted kind path (e.g. `compute.nodes`, `em.services`).
- `<resource_name>` — the resource key, prefixed with `$`.
- `<parameter>` — the field to extract from the resource (e.g. `uuid`, `value`, `description`).

If `:<parameter>` is omitted, the entire resource object is returned.

**Inline template** — a string starting with `f"`:

```yaml
content: |
  f"
  description={$test_import_node.imports.$shared_node:description}
  "
```

Inside an `f"..."` string, `{$link:field}` placeholders are substituted with the resolved value. This is
useful when you need to embed a link value inside a larger string (e.g. a config file body).

### Common parameters

| Parameter | Description |
|---|---|
| `:uuid` | The resource's UUID. Commonly used to link resources together (e.g. a service to its target node). |
| `:value` | The resource's value. Used for variables (`$core.vs.variables`). |
| `:description` | The resource's description field. |
| `:default_network:ipv4` | The IPv4 address of a node's default network interface. |

Any field from the resource's actual state can be accessed as a parameter, including nested fields
separated by `:`.

### Dynamic tracking

Links are not resolved once and forgotten. The platform **continuously tracks** linked fields and
re-renders dependent resources when values change. This is critical for dynamic infrastructure:

- A node's IP address may change after a reboot or migration.
- A variable's value may be updated by an administrator.
- A load balancer's backend pool may gain or lose endpoints.

When a linked field changes, the platform updates the `target_state` of every dependent resource and
reconciles the actual state to match. For example, if a node's IP changes, every config file and load
balancer backend referencing that IP is automatically updated.

---

## Element lifecycle

### From manifest to element

Installing a manifest creates an **element** — a running instance of the declared resources:

1. **Install** — `Manifest.install()` parses the YAML and applies it in order: requirements, imports,
   resources, then exports. Each resource starts in `NEW` status.
2. **Reconcile** — a reconciliation loop ticks every few seconds. For each resource, it renders the
   `target_state` (resolving links and templates), creates or updates a `TargetResource`, and an agent on
   the target node applies the state to the real system.
3. **Converge** — the agent reports back an `actual_resource`. When the actual state matches the target
   state, the resource transitions to `ACTIVE`.

### Status lifecycle

Resources and elements share a status lifecycle:

```text
NEW → IN_PROGRESS → ACTIVE
```

There is no `ERROR` state at the resource level — a resource that cannot converge stays `IN_PROGRESS`.
Some resource kinds (services, configs) track a richer lifecycle with an additional `ERROR` status.

### Dynamic infrastructure

Infrastructure is not static. Nodes can go down, IP addresses change, new nodes join a set. The platform
handles this by continuously reconciling:

- **Links track changes** — when a linked field changes (e.g. a node's IP), the platform re-renders all
  dependent resources and pushes updated configurations.
- **Sets scale** — a `$core.compute.sets` resource maintains a target replica count. If a node in the set
  fails, the platform provisions a replacement.
- **Variables adapt** — `$core.vs.variables` can be updated by administrators. Elements importing those
  variables automatically pick up new values on the next reconciliation cycle.

This is why links are fundamental to the manifest model: they let the platform maintain a declarative
desired state while the underlying infrastructure shifts underneath.

### Upgrade and uninstall

- **Upgrade** — installing a new version of an element updates its resources, imports, and exports in
  place. Removed resources are deleted; new ones are created.
- **Uninstall** — an element can only be removed if no other elements declare it in their `requirements`.
  The platform checks for dependents before removal.

---

## See also

- [Resource Reference](resources.md) — complete list of resource kinds and their fields
- [Export/Import Guide](../core-developer-guide/exports.md) — detailed export/import mechanics
- [Core Developer Guide](../core-developer-guide/core-guide.md) — internal architecture, reconciliation loop
- [Application Developer Guide](../app-developer-guide/app-guide.md) — building and deploying elements
- [Troubleshooting](../usage/troubleshooting.md) — common manifest and element issues
