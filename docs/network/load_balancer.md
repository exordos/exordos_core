---
icon: lucide/share-2
---
# Load Balancer as a Service API

```mermaid
graph TD
    %% Main Load Balancer entity
    LB[Load Balancer]

    %% Vhost components
    V1[Vhost]
    V2[Vhost]

    %% Route components
    R1[Route]
    R2[Route]
    R3[Route]

    %% Backend Pool components
    BP1[Backend Pool]
    BP2[Backend Pool]

    %% Relationships showing the complete structure
    LB -->|contains| V1
    LB -->|contains| V2
    V1 -->|defines| R1
    V1 -->|defines| R2
    V2 -->|defines| R3
    R1 -->|uses| BP1
    R2 -->|uses| BP2
    R3 -->|uses| BP1

    %% Legend
    class LB main
    class V1,V2 component
    class R1,R2,R3 route
    class BP1,BP2 backend
```

## Overview

The Load Balancer service provides a REST-based API for creating and managing load balancer configurations. It supports both Layer 4 (L4) and Layer 7 (L7) protocols with various routing capabilities.

## Core Components

- `Load balancer`: an LB logical instance
- `Vhost`: how to listen ingress
- `Route`: how to enrich requests and where to route them (for example - on which backend)
- `Backend pools`: addresses where we may send requests to

### Load Balancer (LB)

The main load balancer entity that manages:

- Status (NEW, IN_PROGRESS, ACTIVE, ERROR)
- IP addresses
- Type configuration:
    - `core` - VM-based LB
    - `core_agent` - LB will be run on exordos_core instance itself

### Vhost

Virtual host configuration that defines:

- Protocol (http, https, tcp, udp)
- Port
- Domains (for L7 protocols)
- Certificate (for HTTPS)
- External sources (SSH forwarding)

### Backend Pool

Backend pool that manages:

- List of backend endpoints
- Load balancing algorithm (roundrobin)

### Route

Routing rules that define how traffic is handled:

- Condition types
    - `prefix` - nginx-like regex prefixes
    - `exact` - plain equal rule
    - `regex` - nginx-like regex
    - `raw` - used to set backend pools for L4
- Actions
    - `backend` - send to backend
    - `redirect` - return http redirect
    - `local_dir` - serve static from local dir on LB
    - `local_dir_download` - download tar.gz/zstd on LB itself, unpack it and serve data from local dir
- Modifiers (headers, rewrite rules)
    - `headers` - modify headers
        - `auto_header`:
            - X-Forwarded-For
            - X-Forwarded-Port
            - X-Forwarded-Proto
            - X-Forwarded-Prefix
        - `set_header`: set static header

## API Structure

### Creating a Load Balancer

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

### Creating a Virtual Host

```json
{
  "name": "web-vhost",
  "description": "Web server vhost",
  "protocol": "http",
  "port": 80,
  "domains": ["example.com", "www.example.com"],
}
```

#### External sources

You may use external sources to forward traffic from LB to remote systems (use proxy_protocol_from to enable the PROXY protocol and pass the real client IP to the backend; Nginx will be started on the remote system):

```json
{
  "name": "web-vhost",
  "description": "Web server vhost",
  "protocol": "http",
  "port": 80,
  "domains": ["example.com", "www.example.com"],
  "proxy_protocol_from": "127.0.0.1/32",
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

##### Requirements

###### SSH

Tested on Ubuntu 24, prepare remote system:

```console
# We may use nginx with proxy_protocol and socat for udp
apt install nginx-full socat

# Apply binding to privileged ports
echo 'net.ipv4.ip_unprivileged_port_start=0' > /etc/sysctl.d/50-unprivileged-ports.conf
sysctl --system
```

### Creating a Backend Pool

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

### Creating Routes

#### Prefix-based Route

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

#### Exact Match Route

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

## Validation Rules

### Vhost Validation

- L7 protocols (http, https) must have at least one domain
- HTTPS protocol requires a certificate
- L4 protocols don't support domains or certificates
- Protocol+port combinations must be unique

### Route Validation

- L7 protocols can't have raw routes
- L4 protocols can only have raw routes
- Routes must be validated against parent vhost constraints

## Element manifest example

Examples:

- [Jitsi element](https://github.com/infraguys/exordos_basic/blob/master/exordos/manifests/basic.yaml.j2)

 Basic manifest for https site (let's imagine that some nodes are already exist):

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
