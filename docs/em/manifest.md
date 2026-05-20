# Manifest

## Example manifest

```yaml
name: "core_service_example"
description: "Exordos Core Service Example"
schema_version: 1
version: "1.0.3"
api_version: "v1"

requirements:  # Requirements of the manifest
  core:
    from_version: "0.0.0"
    to_version: "1.0.0"

resources: 
  $core.compute.nodes:
    example_node:
      name: "example-service-node"
      description: "Example service node"
      cores: "$core_service_example.imports.$var_default_cores:value"
      ram: "$core_service_example.imports.$var_default_ram:value"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      disk_spec:
        kind: "root_disk"
        size: 10
        image: "{{ base_image_url | default('https://repo.exordos.com/exordos-base/0.4.1/exordos-base.raw.gz') }}"
  
  $core.em.services:
    example_service:
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      name: "example-service"
      path: "/usr/bin/python3 -m http.server 8080"
      user: "ubuntu"
      group: "ubuntu"
      target:
        kind: "node"
        node: "$core_service_example.compute.nodes.$example_node:uuid"
      service_type:
        kind: "simple"
        count: 1
      target_status: "enabled"

exports:
  my_service:
    link: "$core_service_example.em.services.$example_service"
    kind: "resource"
  my_node:
    link: "$core_service_example.em.services.$example_node"
    
imports:
  var_default_cores:
    element: "$core"
    kind: "resource"
    link: "$core.vs.variables.$default_cores"
  var_default_ram:
    element: "$core"
    kind: "resource"
    link: "$core.vs.variables.$default_ram"
```

## name

The name of the manifest.

## description

The description of the manifest.

## schema_version

The schema version of the manifest, now `1`.

## version

Version of the manifest, see docs in [exordos_devtools](https://infraguys.github.io/exordos_devtools/)

## api_version

API version of the manifest, now `v1`

## requirements

```yaml
core:
  from_version: "0.0.0"
  to_version: "1.0.0"
```

Requirements are used to specify the versions of the elements that are required to run the manifest.

## Resources

Resources are used to specify the resources that are required to run the manifest.

## exports

```yaml
my_service:
  link: "$core_service_example.em.services.$example_service"
  kind: "resource" # kind of the export, now only `resource` is supported, may be omitted
```

## imports

```yaml
var_default_cores:
  element: "$core"  # element, from that the import is made
  kind: "resource" # kind of the import, now only `resource` is supported, may be omitted
  link: "$core.vs.variables.$default_cores"   # link to the resource (element, that the import is made, maybe be another)
```

Imports are used to specify the resources that are imported by the manifest.
