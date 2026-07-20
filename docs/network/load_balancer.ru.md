---
icon: lucide/share-2
---
# API Load Balancer as a Service

```mermaid
graph TD
    %% Основная сущность Load Balancer
    LB[Load Balancer]

    %% Компоненты Vhost
    V1[Vhost]
    V2[Vhost]

    %% Компоненты Route
    R1[Route]
    R2[Route]
    R3[Route]

    %% Компоненты Backend Pool
    BP1[Backend Pool]
    BP2[Backend Pool]

    %% Отношения, показывающие полную структуру
    LB -->|содержит| V1
    LB -->|содержит| V2
    V1 -->|определяет| R1
    V1 -->|определяет| R2
    V2 -->|определяет| R3
    R1 -->|использует| BP1
    R2 -->|использует| BP2
    R3 -->|использует| BP1

    %% Легенда
    class LB main
    class V1,V2 component
    class R1,R2,R3 route
    class BP1,BP2 backend
```

## Обзор

Сервис Load Balancer предоставляет REST API для создания и управления конфигурациями балансировщиков нагрузки. Он поддерживает протоколы Layer 4 (L4) и Layer 7 (L7) с различными возможностями маршрутизации.

## Основные компоненты

- `Load balancer`: логический экземпляр LB
- `Vhost`: как принимать входящий трафик
- `Route`: как обогащать запросы и куда их направлять (например, на какой backend)
- `Backend pools`: адреса, куда мы можем отправлять запросы

### Load Balancer (LB)

Основная сущность балансировщика нагрузки, управляющая:

- Статусом (NEW, IN_PROGRESS, ACTIVE, ERROR)
- IP-адресами
- Конфигурацией типа:
    - `core` — VM-based LB
    - `core_agent` — LB будет запущен на самом экземпляре exordos_core

### Vhost

Конфигурация виртуального хоста, определяющая:

- Протокол (http, https, tcp, udp)
- Порт
- Домены (для L7 протоколов)
- Сертификат (для HTTPS)
- Внешние источники (SSH forwarding)

### Backend Pool

Пул бэкендов, управляющий:

- Список конечных точек бэкендов
- Алгоритм балансировки нагрузки (roundrobin)

### Route

Правила маршрутизации, определяющие, как обрабатывается трафик:

- Типы условий
    - `prefix` — nginx-подобные regex префиксы
    - `exact` — простое точное совпадение
    - `regex` — nginx-подобный regex
    - `raw` — используется для установки пулов бэкендов для L4
- Действия
    - `backend` — отправить на бэкенд
    - `redirect` — вернуть http-редирект
    - `local_dir` — отдавать статические файлы из локальной директории на LB
    - `local_dir_download` — скачать tar.gz/zstd на сам LB, распаковать и отдавать данные из локальной директории
- Модификаторы (заголовки, правила rewrite)
    - `headers` — модифицировать заголовки
        - `auto_header`:
            - X-Forwarded-For
            - X-Forwarded-Port
            - X-Forwarded-Proto
            - X-Forwarded-Prefix
        - `set_header`: установить статический заголовок

## Структура API

### Создание балансировщика нагрузки

```json
{
  "name": "my-load-balancer",
  "description": "Production load balancer",
  "type": {
    "kind": "core",
    "cpu": 2,
    "ram": 1024,
    "disk_size": 20,
    "nodes_number": 2
  }
}
```

### Создание виртуального хоста

```json
{
  "name": "web-vhost",
  "description": "Web server vhost",
  "protocol": "http",
  "port": 80,
  "domains": ["example.com", "www.example.com"],
  "external_sources": [
    {
      "kind": "ssh_forward",
      "host": "10.0.0.10",
      "port": 22,
      "user": "admin",
      "private_key": "-----BEGIN RSA PRIVATE KEY-----\\n..."
    }
  ]
}
```

#### Требования к внешним источникам

##### SSH

Проверено на Ubuntu 24, подготовьте удалённую систему:

```console
# Можем использовать nginx с proxy_protocol и socat для udp
apt install nginx-full socat

# Применить привязку к привилегированным портам
echo 'net.ipv4.ip_unprivileged_port_start=0' > /etc/sysctl.d/50-unprivileged-ports.conf
sysctl --system
```

### Создание пула бэкендов

```json
{
  "name": "web-backend-pool",
  "endpoints": [
    {
      "kind": "host",
      "host": "10.0.1.10",
      "port": 80,
      "weight": 1
    },
    {
      "kind": "host",
      "host": "10.0.1.11",
      "port": 80,
      "weight": 1
    }
  ],
  "balance": "roundrobin"
}
```

### Создание маршрутов

#### Маршрут на основе префикса

```json
{
  "name": "api-prefix-route",
  "condition": {
    "kind": "prefix",
    "value": "/api",
    "actions": [
      {
        "kind": "backend",
        "pool": "backend-pool-uuid"
      }
    ]
  }
}
```

#### Маршрут с точным совпадением

```json
{
  "name": "exact-route",
  "condition": {
    "kind": "exact",
    "value": "/login",
    "actions": [
      {
        "kind": "redirect",
        "url": "https://example.com/login",
        "code": 301
      }
    ]
  }
}
```

## Правила валидации

### Валидация Vhost

- L7 протоколы (http, https) должны иметь хотя бы один домен
- HTTPS протокол требует сертификат
- L4 протоколы не поддерживают домены или сертификаты
- Комбинации protocol+port должны быть уникальными

### Валидация маршрутов

- L7 протоколы не могут иметь raw маршруты
- L4 протоколы могут иметь только raw маршруты
- Маршруты должны проходить валидацию относительно ограничений родительского vhost

## Пример манифеста элемента

Примеры:

- [Jitsi element](https://github.com/infraguys/exordos_basic/blob/master/exordos/manifests/basic.yaml.j2)

Базовый манифест для https-сайта (предположим, что некоторые узлы уже существуют):

```yaml
...
requirements:
  core:
    from_version: "0.0.0"

imports:
  core_public_domain:
    element: "$core"
    kind: "resource"
    link: "$core.dns.domains.$public_domain"

  $core.network.lb:
    basic_lb:
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      type:
        kind: core
        ram: 1024
        cpu: 2
  $core.network.lb.$basic_lb.backend_pools:
    basic_backend_http:
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      parent: $core.network.lb.$basic_lb:uuid
      endpoints:
        - kind: host
          host: $core.compute.nodes.$exordos_basic:default_network:ipv4
          port: 80
  $core.network.lb.$basic_lb.vhosts:
    basic_http:
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      parent: $core.network.lb.$basic_lb:uuid
      domains:
        - f"example.{$basic.imports.$core_public_domain:name}"
        - f"www.example.{$basic.imports.$core_public_domain:name}"
      protocol: http
      port: 80
    basic_https:
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      parent: $core.network.lb.$basic_lb:uuid
      domains:
        - f"example.{$basic.imports.$core_public_domain:name}"
        - f"www.example.{$basic.imports.$core_public_domain:name}"
      protocol: https
      port: 443
      cert:
        kind: raw
        crt: $core.secret.certificates.basic_cert:cert
        key: $core.secret.certificates.basic_cert:key
  $core.network.lb.$basic_lb.vhosts.$basic_http.routes:
    basic_http_route:
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      parent: $core.network.lb.$basic_lb.vhosts.$basic_http:uuid
      condition:
        kind: prefix
        value: /
        actions:
          - kind: redirect
            url: f"https://example.{$basic.imports.$core_public_domain:name}"
  $core.network.lb.$basic_lb.vhosts.$basic_https.routes:
    basic_https_route:
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      parent: $core.network.lb.$basic_lb.vhosts.$basic_https:uuid
      condition:
        kind: prefix
        value: /
        actions:
          - kind: backend
            pool: $core.network.lb.$basic_lb.backend_pools.$basic_backend_https:uuid
  $basic.imports.$core_public_domain.records:
    basic_a_record:
      domain: "$basic.imports.$core_public_domain:uuid"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      type: "A"
      record:
        kind: "A"
        name: "example"
        address: "$core.network.lb.$basic_lb:index(ipsv4, 0)"
  $core.secret.certificates:
    basic_cert:
      name: basic_cert
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      email: user@exordos.com
      domains:
        f"example.{$basic.imports.$core_public_domain:name}"
        f"*.example.{$basic.imports.$core_public_domain:name}"
```
