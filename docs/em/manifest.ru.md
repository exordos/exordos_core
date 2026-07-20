# Манифест

## Пример манифеста

```yaml
name: "core_service_example"
description: "Пример сервиса Exordos Core"
schema_version: 1
version: "1.0.3"
api_version: "v1"

requirements:  # Требования манифеста
  core:
    from_version: "0.0.0"
    to_version: "1.0.0"

resources: 
  $core.compute.nodes:
    example_node:
      name: "example-service-node"
      description: "Узел примера сервиса"
      cores: "$core_service_example.imports.$var_default_cores:value"
      ram: "$core_service_example.imports.$var_default_ram:value"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      disk_spec:
        kind: "root_disk"
        size: 10
        image: "{{ base_image_url | default('https://repo.exordos.com/exordos-base/1.1.0/exordos-base.raw.zst') }}"
  
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

Имя манифеста.

## description

Описание манифеста.

## schema_version

Версия схемы манифеста, сейчас `1`.

## version

Версия манифеста, см. документацию в [exordos_devtools](https://infraguys.github.io/exordos_devtools/)

## api_version

Версия API манифеста, сейчас `v1`

## requirements

```yaml
core:
  from_version: "0.0.0"
  to_version: "1.0.0"
```

Требования используются для указания версий элементов, необходимых для запуска манифеста.

## Resources

Ресурсы используются для указания ресурсов, необходимых для запуска манифеста.

## exports

```yaml
my_service:
  link: "$core_service_example.em.services.$example_service"
  kind: "resource" # вид экспорта, сейчас поддерживается только `resource`, может быть опущено
```

## imports

```yaml
var_default_cores:
  element: "$core"  # элемент, из которого выполняется импорт
  kind: "resource" # вид импорта, сейчас поддерживается только `resource`, может быть опущено
  link: "$core.vs.variables.$default_cores"   # ссылка на ресурс (элемент, из которого выполняется импорт, может быть другим)
```

Импорты используются для указания ресурсов, импортируемых манифестом.
