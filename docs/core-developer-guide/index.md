---
title: Core Developer Guide
---

This guide describes the internal architecture of Exordos Core: how elements, manifests, and resources are
stored and reconciled onto compute nodes. It's aimed at people working on Exordos Core itself. If you want
to build an element on top of the platform instead, see the
[Application Developer Guide](../app-developer-guide/index.md).

## Elements, manifests, and resources

Installing an element (`exordos em elements install <manifest.yaml>`) creates a handful of related
records, all defined in `exordos_core/elements/dm/models.py`:

| Model | Table | Purpose |
|---|---|---|
| `Manifest` | `em_manifests` | The parsed YAML manifest (resources, imports, exports, requirements) |
| `Element` | `em_elements` | The installed element (name, version, status) |
| `Resource` | `em_resources` | A single declared resource inside an element |
| `Import` | `em_imports` | Which resource of which element is imported |
| `Export` | `em_exports` | A resource published for other elements to import |

## The element engine

`ElementEngine` is an in-memory registry of every installed element and its resources, loaded from the
database via `load_from_database()`. Each element is a `Namespace`, and resources inside it are addressed
by their link string (for example `$my_element.compute.nodes.$my_node`). The registry is reloaded whenever
a manifest is installed, upgraded, or uninstalled, and lazily on the reconciliation loop's first iteration.

## Reconciliation: from declared resource to running node

Reconciliation is driven by `ElementManagerBuilder` (`elements/services/builders.py`), a service loop that
ticks roughly every 3 seconds:

1. Iterate every resource known to the element engine.
2. Render the resource's `value` into a `target_state`, resolving `$link` references and `f"..."`
   interpolations (see [Manifest value rendering](#manifest-value-rendering)).
3. Create or update a `TargetResource` row (table `ua_target_resources`, from `gcl_sdk`) — this is the
   contract with the node-side agent.
4. A Universal Agent running on the target compute node watches `ua_target_resources` for records of its
   own `kind`, applies them to the real system (systemd unit, VM, disk, config file, and so on), and
   writes back an `actual_resource`.
5. `ElementManagerBuilder` compares the hash of `actual_resource` against `target_state` and updates
   `Resource.status` accordingly.
6. An element's overall status is derived from the status of all its resources.

## Status lifecycle

`Element` and `Resource` share a `Status` enum with three values: `NEW → IN_PROGRESS → ACTIVE`. There is no
`ERROR` state at this level — a resource that can't converge simply stays `IN_PROGRESS`.

Some resource kinds track a richer lifecycle of their own. `Service` (table `em_services`) and `Config`
(table `config_configs`) both add an `ERROR` status alongside `NEW`/`IN_PROGRESS`/`ACTIVE`, since those
subsystems can detect and report a failed apply.

## Manifest value rendering

`_render_value` (`elements/dm/models.py`) turns a manifest string into a concrete value:

- A string starting with `$` is resolved as a resource link.
- A string starting with `f"` is treated as an inline template: `{$element.type.$name:field}`
  placeholders inside it are substituted.
- Any other string is returned unchanged.

A manifest author who forgets the `f"` prefix does not get a silently empty value — the literal `{$...}`
text is left in the rendered output, wherever that value ends up (a config file, a service command line,
and so on). See [Troubleshooting](../usage/troubleshooting.md) for the symptoms this produces.

## Core resource types

- `$core.compute.nodes` / `$core.compute.sets` — virtual machines and node groups (KVM/QEMU)
- `$core.em.services` — systemd services on a node or node set
- `$core.vs.variables` — the Variable Store, used for platform-wide defaults
- `$core.config.configs` — files delivered to a node; requires `project_id` and `body.kind`, see
  [Troubleshooting](../usage/troubleshooting.md)

## See also

- [Manifest reference](../em/manifest.md)
- [Service as a Service API](../em/service.md)
- [Troubleshooting](../usage/troubleshooting.md)
