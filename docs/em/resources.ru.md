---
icon: lucide/layers
---
# Справочник ресурсов

Эта страница документирует наиболее распространённые типы ресурсов, доступные в манифестах Exordos Core.
Каждый тип — это ключ верхнего уровня в секции `resources` манифеста. Структуру манифеста и концепцию
ресурсов см. в [Руководстве по манифестам](manifest.md). Полный набор типов, допускаемых валидацией
манифеста, определён в `exordos/manifests/specification/full_spec.yaml`.

## Обзор

| Тип | Описание |
|---|---|
| [`$core.compute.nodes`](#corecomputenodes) | Виртуальные машины (KVM/QEMU) |
| [`$core.compute.sets`](#corecomputesets) | Группы виртуальных машин с целевым количеством реплик |
| [`$core.em.services`](#coreemservices) | Systemd-сервисы на узле или группе узлов |
| [`$core.config.configs`](#coreconfigconfigs) | Конфигурационные файлы, доставляемые на узел |
| [`$core.vs.profiles`](#corevsprofiles) | Профили Variable Store (например, develop, small, medium, large) |
| [`$core.vs.variables`](#corevsvariables) | Переменные Variable Store с profile-based или selector-based сеттерами |
| [`$core.vs.values`](#corevsvalues) | Значения Variable Store, привязанные к переменным |
| [`$core.dns.domains`](#corednsdomains) | DNS-домены |
| [`$core.dns.domains.$name.records`](#corednsdomainsnamerecords) | DNS-записи внутри домена |
| [`$core.network.lb`](#corenetworklb) | Балансировщики нагрузки |
| [`$core.network.lb.$name.backend_pools`](#corenetworklbnamebackend_pools) | Пулы бэкендов балансировщика |
| [`$core.network.lb.$name.vhosts`](#corenetworklbnamevhosts) | Виртуальные хосты балансировщика |
| [`$core.network.lb.$name.vhosts.$name.routes`](#corenetworklbnamevhostsnameroutes) | Маршруты балансировщика внутри vhost |
| [`$core.secret.certificates`](#coresecretcertificates) | TLS-сертификаты (ACME через DNS-01) |
| [`$core.secret.passwords`](#coresecretpasswords) | Пароли (авто-генерация или вручную) |
| [`$core.iam.organizations`](#coreiamorganizations) | IAM-организации |
| [`$core.iam.organizations.$name.members`](#coreiamorganizationsnamemembers) | Члены организации |
| [`$core.iam.projects`](#coreiamprojects) | IAM-проекты |
| [`$core.iam.users`](#coreiamusers) | IAM-пользователи |
| [`$core.iam.role_bindings`](#coreiamrole_bindings) | IAM-привязки ролей |

---

## $core.compute.nodes

Виртуальная машина, создаваемая вычислительным сервисом платформы (KVM/QEMU). Это самый частый тип
ресурса — он представляет отдельную ВМ, на которой работает ваше приложение.

### Поля

| Поле | Тип | Описание |
|---|---|---|
| `name` | string | Имя хоста узла. |
| `description` | string | Человекочитаемое описание. |
| `cores` | integer | Количество ядер CPU. |
| `ram` | integer | Объём RAM в МБ. |
| `project_id` | uuid | UUID проекта. |
| `disk_spec` | object | Конфигурация дисков (см. ниже). |
| `node_type` | string | `VM` (по умолчанию) или `HW` для bare metal. |
| `hostname` | string | Необязательный явный hostname. По умолчанию `name`. |
| `placement_policies` | array | Необязательный список UUID `PlacementPolicy` для пиннинга/ограничения размещения. |

### Дисковая спецификация

Поле `disk_spec` поддерживает два вида:

**`root_disk`** — единый корневой диск, создаваемый из образа:

```yaml
disk_spec:
  kind: "root_disk"
  size: 10
  image: "https://repo.exordos.com/exordos-base/1.1.0/exordos-base.raw.zst"
  speed: "hot"      # необязательно: cold | warm (по умолчанию) | hot
  ephemeral: true   # необязательно: по умолчанию false
```

**`disks`** — несколько дисков, каждый со своим размером и необязательным образом/меткой:

```yaml
disk_spec:
  kind: "disks"
  disks:
    - size: 10
      image: "https://repo.exordos.com/exordos-base/1.1.0/exordos-base.raw.zst"
    - size: 10
      label: data
      speed: "cold"
      ephemeral: false
```

Первый диск — корневой; последующие — диски данных. Поле `label` необязательно и используется для
идентификации диска внутри узла.

### Размещение дисков

Каждый диск (корневой или дополнительный) поддерживает два необязательных свойства для выбора пула хранения:

- `speed` — `cold`, `warm` (по умолчанию) или `hot`.
- `ephemeral` — `true` или `false` (по умолчанию `false`).

Шедулер предпочитает пул с точным совпадением `speed`/`ephemeral` и достаточным свободным местом. Если
точного совпадения нет или подходящий пул заполнен, размещение переходит на любой пул со свободным
местом, а не завершается ошибкой.

### Пример

```yaml
resources:
  $core.compute.nodes:
    app_node:
      name: "my-app-node"
      description: "Node for my application"
      cores: 2
      ram: 2048
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      disk_spec:
        kind: "root_disk"
        size: 10
        image: "{{ base_image_url | default('https://repo.exordos.com/exordos-base/1.1.0/exordos-base.raw.zst') }}"
```

### Замечания

- Поле `image` может быть шаблоном Jinja2 (рендерится при сборке) или прямым URL.
- Используйте `:default_network:ipv4` для ссылки на IP-адрес узла из других ресурсов:
  `$my_app.compute.nodes.$app_node:default_network:ipv4`.
- Узлы, созданные через `$core.compute.sets`, имеют поле `node_set` автоматически; не задавайте его
  вручную для отдельных узлов.

---

## $core.compute.sets

Группа виртуальных машин с общей конфигурацией, управляемая как единое целое. Платформа поддерживает
целевое количество реплик (`replicas`) — если узел выходит из строя, замена поднимается автоматически.

### Поля

| Поле | Тип | Описание |
|---|---|---|
| `name` | string | Имя группы узлов. |
| `description` | string | Человекочитаемое описание. |
| `cores` | integer | Ядра CPU на реплику. |
| `ram` | integer | RAM на реплику в МБ. |
| `replicas` | integer | Целевое количество узлов (по умолчанию: 1). |
| `project_id` | uuid | UUID проекта. |
| `disk_spec` | object | Конфигурация дисков (тот же формат, что у `$core.compute.nodes`). |

### Пример

```yaml
resources:
  $core.compute.sets:
    web_set:
      name: "web-set"
      description: "Web server cluster"
      cores: 2
      ram: 2048
      replicas: 3
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      disk_spec:
        kind: "root_disk"
        size: 10
        image: "{{ base_image_url | default('https://repo.exordos.com/exordos-base/1.1.0/exordos-base.raw.zst') }}"
```

### Замечания

- Каждая реплика — полноценная ВМ с указанными `cores`, `ram` и `disk_spec`.
- Поле `replicas` может быть ссылкой на переменную: `replicas: $core.vs.variables.$default_replicas:value`.
  Это позволяет масштабировать группу, меняя переменную, не редактируя манифест.
- Отдельные узлы в группе адресуются через поле `nodes` группы, но в большинстве случаев вы ссылаетесь
  на группу целиком (например, как target сервиса).

---

## $core.em.services

Systemd-сервис, работающий на целевом узле или группе узлов. Платформа создаёт systemd-юнит на цели и
управляет его жизненным циклом.

### Поля

| Поле | Тип | Описание |
|---|---|---|
| `name` | string | Имя сервиса. |
| `description` | string | Человекочитаемое описание. |
| `path` | string | Команда для выполнения. |
| `user` | string | Пользователь для запуска (по умолчанию: `root`). |
| `group` | string | Группа для запуска. |
| `target` | object | Где работает сервис (см. ниже). |
| `service_type` | object | Тип сервиса и количество (см. ниже). |
| `target_status` | string | `enabled` (по умолчанию) или `disabled`. |
| `after` | array | Необязательные зависимости systemd `After=`. |
| `before` | array | Необязательные зависимости systemd `Before=`. |

### Target

Поле `target` указывает, где работает сервис:

```yaml
target:
  kind: "node"
  node: "$my_app.compute.nodes.$app_node:uuid"
```

Для группы узлов:

```yaml
target:
  kind: "node_set"
  node_set: "$my_app.compute.sets.$web_set:uuid"
```

### Тип сервиса

```yaml
service_type:
  kind: "simple"    # или "oneshot"
  count: 1
```

### Пример

```yaml
resources:
  $core.em.services:
    app_service:
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      name: "my-app"
      path: "/usr/bin/python3 -m http.server 8080"
      user: "ubuntu"
      group: "ubuntu"
      target:
        kind: "node"
        node: "$my_app.compute.nodes.$app_node:uuid"
      service_type:
        kind: "simple"
        count: 1
      target_status: "enabled"
```

### Замечания

- Сервисы имеют статус `ERROR` в дополнение к стандартному жизненному циклу, поэтому упавшие сервисы
  видны.
- Поле `path` — это полная командная строка; аргументы являются частью строки, а не отдельным полем.
- При таргетинге на группу узлов сервис создаётся на каждом узле группы.

---

## $core.config.configs

Конфигурационный файл, доставляемый на целевой узел. Платформа записывает файл по указанному пути `path`
с указанным содержимым `body` и правами доступа.

### Поля

| Поле | Тип | Описание |
|---|---|---|
| `name` | string | Имя конфига (информационное). |
| `description` | string | Человекочитаемое описание. |
| `path` | string | Абсолютный путь к файлу на целевом узле. |
| `target` | object | Куда доставляется конфиг (тот же формат, что у `target` сервиса). |
| `body` | object | Содержимое файла (см. ниже). |
| `owner` | string | Владелец файла (по умолчанию: `root`). |
| `group` | string | Группа файла (по умолчанию: `root`). |
| `mode` | string | Права доступа, например `0644` (по умолчанию: `0644`). |
| `on_change` | object | Действие при изменении конфига (по умолчанию: `{"kind": "no_action"}`). |
| `project_id` | uuid | UUID проекта. |

### Body

Поле `body` поддерживает два вида:

**`text`** — инлайн-текст:

```yaml
body:
  kind: text
  content: |
    server {
      listen 80;
      server_name example.com;
    }
```

**`template`** — шаблон Jinja2, рендерится с переменными платформы. **Примечание:** этот тип body
пока не реализован (`render()` вызывает `NotImplementedError`); используйте `text` с интерполяцией
`f"..."` вместо него.

```yaml
body:
  kind: template
  template: |
    database_url = postgres://{{ db_host }}:5432/mydb
  variables:
    db_host: localhost
```

### Пример

```yaml
resources:
  $core.config.configs:
    app_config:
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      path: /home/ubuntu/app.conf
      target:
        kind: node
        node: $core.compute.nodes.$app_node:uuid
      body:
        kind: text
        content: |
          f"
          db_host={$my_app.compute.nodes.$app_node:default_network:ipv4}
          "
      owner: "ubuntu"
      group: "ubuntu"
      mode: "0640"
```

### Замечания

- Используйте синтаксис `f"..."` для встраивания значений ссылок в содержимое конфига. Платформа
  разрешает их во время реконсилиации и перезаписывает файл при изменении связанных значений.
- Поле `on_change` задаёт действие при изменении конфига. Поддерживаемые типы: `no_action` (по
  умолчанию) и `shell`. Чтобы перезапустить сервис, используйте shell-команду, например
  `{"kind": "shell", "command": "systemctl restart <service-name>"}`.
- Конфиги имеют статус `ERROR` — если доставка не удалась, конфиг показывает `ERROR`, а не остаётся в
  `IN_PROGRESS`.

---

## $core.vs.profiles

Профиль Variable Store. Профили используются переменными для предоставления разных значений по умолчанию
в зависимости от размера развёртывания (например, `develop`, `small`, `medium`, `large`).

### Поля

| Поле | Тип | Описание |
|---|---|---|
| `name` | string | Имя профиля. |
| `description` | string | Человекочитаемое описание. |
| `profile_type` | string | `GLOBAL` или `ELEMENT`. Глобальные профили доступны всем элементам. |
| `project_id` | uuid | UUID проекта. |

### Пример

```yaml
resources:
  $core.vs.profiles:
    develop:
      name: "develop"
      profile_type: "GLOBAL"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      description: "Development profile"
    small:
      name: "small"
      profile_type: "GLOBAL"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      description: "Small production profile"
```

### Замечания

- Элемент `core` определяет стандартные профили: `develop`, `small`, `medium`, `large`, `legacy`.
- Большинство элементов не определяют свои профили — они импортируют профили core и используют их в
  сеттерах переменных.

---

## $core.vs.variables

Переменная Variable Store. Переменные хранят значения, которые могут меняться во время выполнения, и на
которые могут ссылаться другие ресурсы через ссылки. Поле `setter` определяет, как выбирается значение
переменной.

### Поля

| Поле | Тип | Описание |
|---|---|---|
| `name` | string | Имя переменной. |
| `description` | string | Человекочитаемое описание. |
| `project_id` | uuid | UUID проекта. |
| `setter` | object | Как определяется значение (см. ниже). |

### Виды сеттеров

**`profile`** — значение зависит от активного профиля:

```yaml
setter:
  kind: profile
  fallback_strategy: ignore    # поддерживается только "ignore"
  profiles:
    - profile: $core.vs.profiles.$develop:uuid
      value: 1
    - profile: $core.vs.profiles.$small:uuid
      value: 2
    - profile: $core.vs.profiles.$medium:uuid
      value: 4
```

**`selector`** — значение выбирается из привязанных записей `$core.vs.values`:

```yaml
setter:
  kind: selector
  selector_strategy: latest    # поддерживается только "latest"
```

### Пример

```yaml
resources:
  $core.vs.variables:
    default_cores:
      name: "default_cores"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      setter:
        kind: profile
        fallback_strategy: ignore
        profiles:
          - profile: $core.vs.profiles.$develop:uuid
            value: 1
          - profile: $core.vs.profiles.$small:uuid
            value: 2
          - profile: $core.vs.profiles.$medium:uuid
            value: 4
          - profile: $core.vs.profiles.$large:uuid
            value: 8
```

### Замечания

- Переменные — основной механизм настройки элементов без изменения манифеста.
- Используйте `:value` для ссылки на текущее значение переменной:
  `$core.vs.variables.$default_cores:value`.
- `fallback_strategy: ignore` означает, что переменная не имеет значения, если ни один профиль не
  совпал. В настоящее время поддерживается только `ignore`.
- Переменные на основе профилей разрешаются при установке на основе активного профиля realm.
- Переменные на основе селектора можно менять во время выполнения, создавая/изменяя записи
  `$core.vs.values`.

---

## $core.vs.values

Значение, привязанное к переменной. Значения — это конкретные данные, которые разрешают переменные.
Несколько значений могут быть привязаны к одной переменной (например, из разных источников);
`selector_strategy` переменной определяет, какое выигрывает.

### Поля

| Поле | Тип | Описание |
|---|---|---|
| `name` | string | Имя значения (информационное). |
| `description` | string | Человекочитаемое описание. |
| `value` | any | Фактическое значение (строка, число, список, объект). |
| `variable` | uuid | Переменная, к которой привязано значение. |
| `read_only` | boolean | Если true, значение нельзя перезаписать из других источников. |
| `project_id` | uuid | UUID проекта. |

### Пример

```yaml
resources:
  $core.vs.values:
    int_value:
      name: "int value"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      value: 1

    list_value:
      name: "list value"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      value: [1, 2, 3]

    selector_value_foo:
      name: "selector value foo"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      value: "foo"
      variable: $core.vs.variables.$var_selector:uuid
```

### Замечания

- Значение без поля `variable` — самостоятельное значение; на него можно ссылаться напрямую, но оно не
  участвует в селекторе переменной.
- Когда несколько значений привязаны к одной переменной, `selector_strategy: latest` выбирает последнее
  созданное. В настоящее время поддерживается только `latest`.

---

## $core.dns.domains

DNS-домен, управляемый встроенным DNS-сервером платформы.

### Поля

| Поле | Тип | Описание |
|---|---|---|
| `name` | string | Имя домена (например, `local.example.com`). |
| `project_id` | uuid | UUID проекта. |
| `sync_to_ecosystem` | boolean | Если true, синхронизировать домен с DNS экосистемы. |

### Пример

```yaml
resources:
  $core.dns.domains:
    local_domain:
      name: "local.example.com"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
```

### Замечания

- DNS-записи определяются как вложенные ресурсы под доменом (см.
  [`$core.dns.domains.$name.records`](#corednsdomainsnamerecords)).
- UUID домена используется как поле `domain` в ресурсах записей.

---

## $core.dns.domains.$name.records

DNS-запись внутри домена. Путь типа включает ключ родительского домена, например
`$core.dns.domains.$local_domain.records`.

### Поля

| Поле | Тип | Описание |
|---|---|---|
| `domain` | uuid | UUID родительского домена. |
| `type` | string | Тип записи: `A`, `NS`, `SOA`, `TXT`. |
| `record` | object | Данные записи, специфичные для типа (см. ниже). |
| `ttl` | integer | TTL в секундах (по умолчанию: 3600). |
| `prio` | integer | Приоритет (для MX-записей). |
| `disabled` | boolean | Если true, запись не обслуживается. |
| `project_id` | uuid | UUID проекта. |

### Типы записей

**A-запись:**

```yaml
record:
  kind: "A"
  name: "core"
  address: "10.20.0.2"
```

**SOA-запись:**

```yaml
record:
  kind: "SOA"
  name: "@"
  ttl: 3600
  retry: 3600
  expire: 604800
  serial: 0
  refresh: 10800
  primary_dns: "a.misconfigured.dns.server.invalid"
```

### Пример

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
        address: $core.vs.variables.$core_ip_address:value
```

### Замечания

- Поле `domain` должно ссылаться на UUID родительского домена через ссылку.
- Поле `address` может быть ссылкой на переменную или IP узла, что обеспечивает динамическое обновление
  DNS при изменении инфраструктуры.

---

## $core.network.lb

Балансировщик нагрузки. Платформа поднимает nginx-балансировщик (как ВМ) и настраивает его на основе
связанных пулов бэкендов, vhost и маршрутов.

### Поля

| Поле | Тип | Описание |
|---|---|---|
| `name` | string | Имя балансировщика. |
| `description` | string | Человекочитаемое описание. |
| `type` | object | Конфигурация типа LB (см. ниже). |
| `project_id` | uuid | UUID проекта. |

### Тип LB

```yaml
type:
  kind: "core"
  ram: 512          # RAM в МБ для ВМ балансировщика
  cpu: 1            # Ядра CPU
  disk_size: 10     # Размер диска в ГБ
  nodes_number: 1   # Количество узлов LB
```

Если `type` опущен, используются значения по умолчанию: `kind: core`, `ram: 512`, `cpu: 1`,
`disk_size: 10`, `nodes_number: 1`.

### Пример

```yaml
resources:
  $core.network.lb:
    example_lb:
      name: "example-lb"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      type:
        kind: "core"
        ram: 1024
        cpu: 2
```

### Замечания

- Пулы бэкендов, vhost и маршруты определяются как вложенные ресурсы (см. ниже).
- IP-адреса балансировщика доступны через параметр `:ipsv4` после развёртывания.

---

## $core.network.lb.$name.backend_pools

Пул бэкендов — группа эндпоинтов, между которыми распределяется трафик. Определяется как вложенный
ресурс под балансировщиком.

### Поля

| Поле | Тип | Описание |
|---|---|---|
| `name` | string | Имя пула. |
| `description` | string | Человекочитаемое описание. |
| `parent` | uuid | UUID родительского балансировщика. |
| `endpoints` | array | Список объектов эндпоинтов (см. ниже). |
| `balance` | string | Алгоритм балансировки (по умолчанию: `roundrobin`). |
| `project_id` | uuid | UUID проекта. |

### Эндпоинты

```yaml
endpoints:
  - kind: host
    host: $core.compute.nodes.$app_node:default_network:ipv4
    port: 443
    weight: 1
```

### Пример

```yaml
resources:
  $core.network.lb.$example_lb.backend_pools:
    https_pool:
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      parent: $core.network.lb.$example_lb:uuid
      endpoints:
        - kind: host
          host: $core.compute.nodes.$app_node:default_network:ipv4
          port: 443
```

### Замечания

- Поле `host` обычно — ссылка на IP-адрес узла. При изменении IP узла пул бэкендов обновляется
  автоматически.
- Поле `parent` должно ссылаться на UUID родительского балансировщика.

---

## $core.network.lb.$name.vhosts

Виртуальный хост на балансировщике — определяет, как LB обрабатывает трафик для конкретного протокола и
порта.

### Поля

| Поле | Тип | Описание |
|---|---|---|
| `name` | string | Имя vhost. |
| `description` | string | Человекочитаемое описание. |
| `parent` | uuid | UUID родительского балансировщика. |
| `domains` | array | Список доменных имён. |
| `protocol` | string | `http`, `https`, `tcp` или `udp`. |
| `port` | integer | Порт для прослушивания (по умолчанию: 80). |
| `cert` | object | TLS-сертификат (только для `https`, см. ниже). |
| `enabled` | boolean | Активен ли vhost (по умолчанию: true). |
| `project_id` | uuid | UUID проекта. |

### Сертификат

Для HTTPS-vhost укажите сертификат инлайн или через ссылку:

```yaml
cert:
  kind: raw
  crt: |
    -----BEGIN CERTIFICATE-----
    YOUR_CERT_DATA
    -----END CERTIFICATE-----
  key: |
    -----BEGIN PRIVATE KEY-----
    YOUR_KEY_DATA
    -----END PRIVATE KEY-----
```

### Пример

```yaml
resources:
  $core.network.lb.$example_lb.vhosts:
    https_vhost:
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      parent: $core.network.lb.$example_lb:uuid
      domains:
        - example.com
        - www.example.com
      protocol: https
      port: 443
      cert:
        kind: raw
        crt: |
          -----BEGIN CERTIFICATE-----
          YOUR_CERT_DATA
          -----END CERTIFICATE-----
        key: |
          -----BEGIN PRIVATE KEY-----
          YOUR_KEY_DATA
          -----END PRIVATE KEY-----
```

### Замечания

- Маршруты определяются как вложенные ресурсы под vhost (см. ниже).
- Для редиректа HTTP→HTTPS создайте HTTP-vhost на порту 80 с маршрутом-редиректом.

---

## $core.network.lb.$name.vhosts.$name.routes

Маршрут внутри vhost — определяет действие при совпадении запроса с условием.

### Поля

| Поле | Тип | Описание |
|---|---|---|
| `name` | string | Имя маршрута. |
| `description` | string | Человекочитаемое описание. |
| `parent` | uuid | UUID родительского vhost. |
| `condition` | object | Условие совпадения (см. ниже). |
| `enabled` | boolean | Активен ли маршрут (по умолчанию: true). |
| `project_id` | uuid | UUID проекта. |

### Условия

**Префиксное совпадение:**

```yaml
condition:
  kind: prefix
  value: /
  actions:
    - kind: backend
      pool: $core.network.lb.$example_lb.backend_pools.$https_pool:uuid
      protocol:
        kind: https
        verify: false
```

**Редирект:**

```yaml
condition:
  kind: prefix
  value: /
  actions:
    - kind: redirect
      url: https://example.com
```

**Raw (L4, для TCP/UDP vhost):**

```yaml
condition:
  kind: raw
  actions:
    - kind: backend
      pool: $core.network.lb.$example_lb.backend_pools.$udp_pool:uuid
```

### Пример

```yaml
resources:
  $core.network.lb.$example_lb.vhosts.$https_vhost.routes:
    https_route:
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      parent: $core.network.lb.$example_lb.vhosts.$https_vhost:uuid
      condition:
        kind: prefix
        value: /
        actions:
          - kind: backend
            pool: $core.network.lb.$example_lb.backend_pools.$https_pool:uuid
            protocol:
              kind: https
              verify: false
```

### Замечания

- Поле `parent` должно ссылаться на UUID родительского vhost.
- Для L4 (TCP/UDP) vhost используйте `condition.kind: raw`, так как HTTP-пути для сопоставления нет.

---

## $core.secret.certificates

TLS-сертификат, управляемый платформой. Получается автоматически через ACME (Let's Encrypt с DNS-01
challenge с использованием CoreDNS платформы). В настоящее время поддерживается только метод `dns_core`.

### Поля

| Поле | Тип | Описание |
|---|---|---|
| `name` | string | Имя сертификата. |
| `description` | string | Человекочитаемое описание. |
| `email` | string | Email для регистрации ACME. |
| `method` | object | Метод получения (см. ниже). |
| `domains` | array | Список доменов (поддерживает wildcard вроде `*.example.com`). |
| `expiration_threshold` | integer | Дней до истечения для запуска обновления. |
| `overcome_threshold` | boolean | Внутреннее поле, заполняемое из состояния агента; не задаётся через пользовательский API. |
| `constructor` | object | Как хранятся cert/key (по умолчанию: `{"kind": "plain"}`). |
| `project_id` | uuid | UUID проекта. |

### Метод

```yaml
method:
  kind: dns_core    # ACME через DNS-01 challenge с использованием CoreDNS
```

### Пример

```yaml
resources:
  $core.secret.certificates:
    wildcard_cert:
      name: "wildcard-cert"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      email: "admin@example.com"
      method:
        kind: dns_core
      domains:
        - "*.example.com"
        - "example.com"
      expiration_threshold: 30
```

### Замечания

- Метод `dns_core` использует встроенный CoreDNS платформы для автоматического выполнения ACME
  DNS-01 challenge. Домен должен управляться DNS платформы.
- После получения сертификат и ключ сохраняются на агенте целевого узла.
- Используйте `expiration_threshold` для управления автоматическим обновлением. Платформа обновляет
  сертификаты до истечения.

---

## $core.secret.passwords

Пароль, управляемый платформой. Поддерживает автоматическую генерацию (случайный hex, URL-safe base64)
или ручные значения.

### Поля

| Поле | Тип | Описание |
|---|---|---|
| `name` | string | Имя пароля. |
| `description` | string | Человекочитаемое описание. |
| `method` | string | `AUTO_HEX`, `AUTO_URL_SAFE` или `MANUAL`. |
| `value` | string | Значение пароля. Обязательно для `MANUAL`; для авто-методов должно отсутствовать (установка вызывает ошибку). |
| `default_length` | integer | Длина для авто-генерируемых паролей (по умолчанию: 32). |
| `constructor` | object | Как хранится пароль (по умолчанию: `{"kind": "plain"}`). |
| `project_id` | uuid | UUID проекта. |

### Методы

| Метод | Описание |
|---|---|
| `AUTO_HEX` | Случайная hex-строка через `secrets.token_hex()`. Пример: `a1b2c3d4e5f6...` |
| `AUTO_URL_SAFE` | Случайная URL-safe base64 строка через `secrets.token_urlsafe()`. Пример: `a1b2c3d4-e5f6_...` |
| `MANUAL` | Использовать точное значение из поля `value`. |

### Пример

```yaml
resources:
  $core.secret.passwords:
    db_password:
      name: "db-password"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      method: "AUTO_HEX"
      constructor:
        kind: plain
      default_length: 32

    admin_password:
      name: "admin-password"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      method: "MANUAL"
      constructor:
        kind: plain
      value: "my-strong-password"
```

### Замечания

- Авто-генерируемые пароли создаются один раз при установке. Значение доступно через параметр ссылки
  `:value`. При `constructor.kind: plain` (по умолчанию) пароль хранится как есть; настройте другой
  конструктор для защиты при хранении.
- Используйте `AUTO_URL_SAFE` для паролей, которые могут встречаться в URL (API-ключи, токены).

---

## $core.iam.organizations

IAM-организация — сущность верхнего уровня в системе управления доступом платформы.

### Поля

| Поле | Тип | Описание |
|---|---|---|
| `name` | string | Имя организации. |
| `description` | string | Человекочитаемое описание. |
| `info` | object | Произвольные метаданные. |

### Пример

```yaml
resources:
  $core.iam.organizations:
    jdoe_corp:
      name: "jdoe-corp"
      description: "John Corporation"
```

### Замечания

- Организации содержат проекты и членов. Члены определяются как вложенные ресурсы (см. ниже).
- Организация обязательна перед созданием проектов.

---

## $core.iam.organizations.$name.members

Член организации. Определяет роль пользователя в организации.

### Поля

| Поле | Тип | Описание |
|---|---|---|
| `organization` | uuid | UUID родительской организации. |
| `user` | uuid | UUID пользователя. |
| `role` | string | `OWNER` или `MEMBER`. |

### Пример

```yaml
resources:
  $core.iam.organizations.$jdoe_corp.members:
    jdoe_member:
      organization: "$core.iam.organizations.$jdoe_corp:uuid"
      user: $core.iam.users.$jdoe:uuid
      role: "OWNER"
```

### Замечания

- Поля `organization` и `user` обычно — ссылки на UUID соответствующих ресурсов.
- `OWNER` может управлять членами и проектами организации; `MEMBER` имеет доступ только к проектам, в
  которых он назначен.

---

## $core.iam.projects

IAM-проект — логический контейнер для ресурсов. Большинство ресурсов требуют поле `project_id`.

### Поля

| Поле | Тип | Описание |
|---|---|---|
| `name` | string | Имя проекта. |
| `description` | string | Человекочитаемое описание. |
| `organization` | uuid | UUID родительской организации. |

### Пример

```yaml
resources:
  $core.iam.projects:
    jdoe_pr:
      name: "jdoe-pr"
      description: "John environment"
      organization: "$core.iam.organizations.$jdoe_corp:uuid"
```

### Замечания

- Поле `organization` должно ссылаться на UUID существующей организации.
- UUID проекта используется как поле `project_id` в большинстве других типов ресурсов.

---

## $core.iam.users

Учётная запись IAM-пользователя.

### Поля

| Поле | Тип | Описание |
|---|---|---|
| `username` | string | Уникальное имя пользователя. **Обязательно.** |
| `email` | string | Email-адрес. **Обязательно.** |
| `password` | string | Начальный пароль. |
| `first_name` | string | Имя. |
| `last_name` | string | Фамилия. |
| `description` | string | Человекочитаемое описание. |
| `phone` | string | Номер телефона. |
| `type` | string | `user` (по умолчанию), `service` или `anon`. |
| `email_verified` | boolean | Подтверждён ли email. |
| `otp_enabled` | boolean | Включён ли OTP. |

### Пример

```yaml
resources:
  $core.iam.users:
    jdoe:
      username: "jdoe"
      email: "jdoe@corp.com"
      password: "12345678"
      first_name: "John"
      last_name: "Doe"
      description: "production engineer"
```

### Замечания

- Пользователи типа `service` предназначены для аутентификации machine-to-machine (API-клиенты,
  CI/CD-конвейеры).
- Поле `password` устанавливает начальный пароль. Пользователь может изменить его позже через UI или API
  платформы.

---

## $core.iam.role_bindings

Привязка пользователя к роли, опционально с областью действия — проектом.

### Поля

| Поле | Тип | Описание |
|---|---|---|
| `role` | uuid | UUID роли. |
| `user` | uuid | UUID пользователя. |
| `project` | uuid | Необязательно. Если указано, привязка ограничена этим проектом. |

### Пример

```yaml
resources:
  $core.iam.role_bindings:
    jdoe_global_role:
      role: "726f6c65-0000-0000-0000-000000000002"
      user: $core.iam.users.$jdoe:uuid

    jdoe_project_role:
      role: "726f6c65-0000-0000-0000-000000000002"
      user: $core.iam.users.$jdoe:uuid
      project: $core.iam.projects.$jdoe_pr:uuid
```

### Замечания

- Привязка без поля `project` — глобальная: пользователь имеет роль во всех проектах.
- Привязка с полем `project` — ограниченная: пользователь имеет роль только в указанном проекте.
- UUID ролей предопределены платформой. Известные встроенные роли: `newcomer`
  (`726f6c65-0000-0000-0000-000000000001`, по умолчанию для новых пользователей) и `owner`
  (`726f6c65-0000-0000-0000-000000000002`, полные административные привилегии в рамках проекта,
  назначается автоматически при создании проекта). Модель ролей и разрешений см. на странице
  [IAM Permissions](../iam/permissions_overview.md).

---

## См. также

- [Руководство по манифестам](manifest.md) — структура манифеста, ссылки, жизненный цикл
- [Руководство по экспорту/импорту](../core-developer-guide/exports.md) — обмен ресурсами между элементами
- [Руководство разработчика Core](../core-developer-guide/core-guide.md) — внутренняя архитектура
- [Спецификация OpenAPI](../openapi/openapi_user.md) — детали API на уровне полей
