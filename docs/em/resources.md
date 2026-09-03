---
icon: lucide/layers
---
# Resource Reference

This page documents the most common resource kinds available in Exordos Core manifests. Each kind is a
top-level key under the `resources` section of a manifest. For the manifest structure and the resource
concept, see the [Manifest Guide](manifest.md). The complete set of kinds accepted by manifest validation
is defined in `exordos/manifests/specification/full_spec.yaml`.

## Overview

| Kind | Description |
|---|---|
| [`$core.compute.nodes`](#corecomputenodes) | Virtual machines (KVM/QEMU) |
| [`$core.compute.sets`](#corecomputesets) | Groups of virtual machines with a target replica count |
| [`$core.em.services`](#coreemservices) | Systemd services on a node or node set |
| [`$core.config.configs`](#coreconfigconfigs) | Configuration files delivered to a node |
| [`$core.vs.profiles`](#corevsprofiles) | Variable Store profiles (e.g. develop, small, medium, large) |
| [`$core.vs.variables`](#corevsvariables) | Variable Store variables with profile-based or selector-based setters |
| [`$core.vs.values`](#corevsvalues) | Variable Store values bound to variables |
| [`$core.dns.domains`](#corednsdomains) | DNS domains |
| [`$core.dns.domains.$name.records`](#corednsdomainsnamerecords) | DNS records within a domain |
| [`$core.network.lb`](#corenetworklb) | Load balancers |
| [`$core.network.lb.$name.backend_pools`](#corenetworklbnamebackend_pools) | Load balancer backend pools |
| [`$core.network.lb.$name.vhosts`](#corenetworklbnamevhosts) | Load balancer virtual hosts |
| [`$core.network.lb.$name.vhosts.$name.routes`](#corenetworklbnamevhostsnameroutes) | Load balancer routes within a vhost |
| [`$core.secret.certificates`](#coresecretcertificates) | TLS certificates (ACME via DNS-01) |
| [`$core.secret.passwords`](#coresecretpasswords) | Passwords (auto-generated or manual) |
| [`$core.iam.organizations`](#coreiamorganizations) | IAM organizations |
| [`$core.iam.organizations.$name.members`](#coreiamorganizationsnamemembers) | Organization members |
| [`$core.iam.projects`](#coreiamprojects) | IAM projects |
| [`$core.iam.users`](#coreiamusers) | IAM users |
| [`$core.iam.role_bindings`](#coreiamrole_bindings) | IAM role bindings |

---

## $core.compute.nodes

A virtual machine provisioned by the platform's compute service (KVM/QEMU). This is the most common
resource type — it represents a single VM that runs your application.

### Fields

| Field | Type | Description |
|---|---|---|
| `name` | string | Hostname of the node. |
| `description` | string | Human-readable description. |
| `cores` | integer | Number of CPU cores. |
| `ram` | integer | Amount of RAM in MB. |
| `project_id` | uuid | Project UUID. |
| `disk_spec` | object | Disk configuration (see below). |
| `node_type` | string | `VM` (default) or `HW` for bare metal. |
| `hostname` | string | Optional explicit hostname. Defaults to `name`. |
| `placement_policies` | array | Optional list of `PlacementPolicy` UUIDs for pinning/constraining placement. |

### Disk spec

The `disk_spec` field supports two kinds:

**`root_disk`** — a single root disk created from an image:

```yaml
disk_spec:
  kind: "root_disk"
  size: 10
  image: "https://repo.exordos.com/exordos-base/1.1.0/exordos-base.raw.zst"
  speed: "hot"      # optional: cold | warm (default) | hot
  ephemeral: true   # optional: default false
```

**`disks`** — multiple disks, each with its own size and optional image/label:

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

The first disk is the root disk; subsequent disks are data disks. A `label` is optional and used to
identify the disk within the node.

### Disk placement

Each disk (root or additional) accepts two optional properties used to pick a storage pool:

- `speed` — `cold`, `warm` (default), or `hot`.
- `ephemeral` — `true` or `false` (default `false`).

The scheduler prefers a storage pool with an exact `speed`/`ephemeral` match and enough free capacity.
If no pool matches exactly, or the matching pool is full, placement falls back to any pool with room
rather than failing outright.

### Example

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

### Notes

- The `image` field can be a Jinja2 template (rendered at build time) or a direct URL.
- Use `:default_network:ipv4` to reference a node's IP address from other resources:
  `$my_app.compute.nodes.$app_node:default_network:ipv4`.
- Nodes created via `$core.compute.sets` have their `node_set` field set automatically; do not set it
  manually for standalone nodes.

---

## $core.compute.sets

A group of virtual machines that share the same configuration and are managed as a single unit. The
platform maintains the target `replicas` count — if a node fails, a replacement is provisioned
automatically.

### Fields

| Field | Type | Description |
|---|---|---|
| `name` | string | Name of the node set. |
| `description` | string | Human-readable description. |
| `cores` | integer | CPU cores per replica. |
| `ram` | integer | RAM per replica in MB. |
| `replicas` | integer | Target number of nodes (default: 1). |
| `project_id` | uuid | Project UUID. |
| `disk_spec` | object | Disk configuration (same format as `$core.compute.nodes`). |

### Example

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

### Notes

- Each replica is a full VM with the specified `cores`, `ram`, and `disk_spec`.
- The `replicas` field can be a link to a variable: `replicas: $core.vs.variables.$default_replicas:value`.
  This lets you scale the set by changing a variable, without editing the manifest.
- Individual nodes in a set are addressable via the set's `nodes` field, but in most cases you reference
  the set as a whole (e.g. as a service target).

---

## $core.em.services

A systemd service that runs on a target node or node set. The platform creates the systemd unit on the
target and manages its lifecycle.

### Fields

| Field | Type | Description |
|---|---|---|
| `name` | string | Service name. |
| `description` | string | Human-readable description. |
| `path` | string | Command to execute. |
| `user` | string | User to run the service as (default: `root`). |
| `group` | string | Group to run the service as. |
| `target` | object | Where the service runs (see below). |
| `service_type` | object | Service type and count (see below). |
| `target_status` | string | `enabled` (default) or `disabled`. |
| `after` | array | Optional systemd `After=` dependencies. |
| `before` | array | Optional systemd `Before=` dependencies. |

### Target

The `target` field specifies where the service runs:

```yaml
target:
  kind: "node"
  node: "$my_app.compute.nodes.$app_node:uuid"
```

For a node set:

```yaml
target:
  kind: "node_set"
  node_set: "$my_app.compute.sets.$web_set:uuid"
```

### Service type

```yaml
service_type:
  kind: "simple"    # or "oneshot"
  count: 1
```

### Example

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

### Notes

- Services have an `ERROR` status in addition to the standard lifecycle, so failed services are visible.
- The `path` is the full command line — arguments are part of the string, not a separate field.
- When targeting a node set, the service is created on every node in the set.

---

## $core.config.configs

A configuration file delivered to a target node. The platform writes the file at the specified `path`
with the specified `body` and file permissions.

### Fields

| Field | Type | Description |
|---|---|---|
| `name` | string | Config name (informational). |
| `description` | string | Human-readable description. |
| `path` | string | Absolute file path on the target node. |
| `target` | object | Where the config is delivered (same format as service `target`). |
| `body` | object | File content (see below). |
| `owner` | string | File owner (default: `root`). |
| `group` | string | File group (default: `root`). |
| `mode` | string | File mode, e.g. `0644` (default: `0644`). |
| `on_change` | object | Action to take when the config changes (default: `{"kind": "no_action"}`). |
| `project_id` | uuid | Project UUID. |

### Body

The `body` field supports two kinds:

**`text`** — inline text content:

```yaml
body:
  kind: text
  content: |
    server {
      listen 80;
      server_name example.com;
    }
```

**`template`** — a Jinja2 template rendered with platform variables. **Note:** this body kind is not
yet implemented (`render()` raises `NotImplementedError`); use `text` with `f"..."` interpolation
instead.

```yaml
body:
  kind: template
  template: |
    database_url = postgres://{{ db_host }}:5432/mydb
  variables:
    db_host: localhost
```

### Example

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

### Notes

- Use `f"..."` syntax to embed link values inside config content. The platform resolves these at
  reconciliation time and rewrites the file when linked values change.
- The `on_change` field specifies an action to take when the config changes. Supported kinds are
  `no_action` (default) and `shell`. To restart a service, use a shell command, e.g.
  `{"kind": "shell", "command": "systemctl restart <service-name>"}`.
- Configs have an `ERROR` status — if delivery fails, the config shows `ERROR` rather than staying in
  `IN_PROGRESS`.

---

## $core.vs.profiles

A Variable Store profile. Profiles are used by variables to provide different default values depending on
the deployment size (e.g. `develop`, `small`, `medium`, `large`).

### Fields

| Field | Type | Description |
|---|---|---|
| `name` | string | Profile name. |
| `description` | string | Human-readable description. |
| `profile_type` | string | `GLOBAL` or `ELEMENT`. Global profiles are available to all elements. |
| `project_id` | uuid | Project UUID. |

### Example

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

### Notes

- The `core` element defines the standard profiles: `develop`, `small`, `medium`, `large`, `legacy`.
- Most elements do not define their own profiles — they import the core profiles and use them in
  variable setters.

---

## $core.vs.variables

A Variable Store variable. Variables hold values that can change at runtime and be referenced by other
resources via links. The `setter` field determines how the variable's value is chosen.

### Fields

| Field | Type | Description |
|---|---|---|
| `name` | string | Variable name. |
| `description` | string | Human-readable description. |
| `project_id` | uuid | Project UUID. |
| `setter` | object | How the value is determined (see below). |

### Setter kinds

**`profile`** — the value depends on the active profile:

```yaml
setter:
  kind: profile
  fallback_strategy: ignore    # only "ignore" is currently supported
  profiles:
    - profile: $core.vs.profiles.$develop:uuid
      value: 1
    - profile: $core.vs.profiles.$small:uuid
      value: 2
    - profile: $core.vs.profiles.$medium:uuid
      value: 4
```

**`selector`** — the value is selected from bound `$core.vs.values` entries:

```yaml
setter:
  kind: selector
  selector_strategy: latest    # only "latest" is currently supported
```

### Example

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

### Notes

- Variables are the primary mechanism for making elements configurable without changing the manifest.
- Use `:value` to reference a variable's current value: `$core.vs.variables.$default_cores:value`.
- The `fallback_strategy: ignore` means the variable has no value if no profile matches. Use
  `fallback_strategy: default` with a `default` field to provide a fallback.
- Profile-based variables are resolved at install time based on the realm's active profile.
- Selector-based variables can be changed at runtime by creating/modifying `$core.vs.values` entries.

---

## $core.vs.values

A value bound to a variable. Values are the concrete data that variables resolve to. Multiple values can
be bound to the same variable (e.g. from different sources); the variable's `selector_strategy` determines
which one wins.

### Fields

| Field | Type | Description |
|---|---|---|
| `name` | string | Value name (informational). |
| `description` | string | Human-readable description. |
| `value` | any | The actual value (string, integer, list, object). |
| `variable` | uuid | The variable this value is bound to. |
| `read_only` | boolean | If true, the value cannot be overwritten by other sources. |
| `project_id` | uuid | Project UUID. |

### Example

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

### Notes

- A value without a `variable` field is a standalone value — it can be referenced directly but does not
  feed into any variable's selector.
- When multiple values are bound to the same variable, `selector_strategy: latest` picks the most recently
  created one; `selector_strategy: manual` requires an administrator to explicitly select one.

---

## $core.dns.domains

A DNS domain managed by the platform's built-in DNS server.

### Fields

| Field | Type | Description |
|---|---|---|
| `name` | string | Domain name (e.g. `local.example.com`). |
| `project_id` | uuid | Project UUID. |
| `sync_to_ecosystem` | boolean | If true, sync the domain to the ecosystem DNS. |

### Example

```yaml
resources:
  $core.dns.domains:
    local_domain:
      name: "local.example.com"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
```

### Notes

- DNS records are defined as nested resources under the domain (see
  [`$core.dns.domains.$name.records`](#corednsdomainsnamerecords)).
- The domain's UUID is used as the `domain` field in record resources.

---

## $core.dns.domains.$name.records

A DNS record within a domain. The kind path includes the parent domain's key name, e.g.
`$core.dns.domains.$local_domain.records`.

### Fields

| Field | Type | Description |
|---|---|---|
| `domain` | uuid | The parent domain UUID. |
| `type` | string | Record type: `A`, `NS`, `SOA`, `TXT`. |
| `record` | object | Type-specific record data (see below). |
| `ttl` | integer | TTL in seconds (default: 3600). |
| `prio` | integer | Priority (for MX records). |
| `disabled` | boolean | If true, the record is not served. |
| `project_id` | uuid | Project UUID. |

### Record types

**A record:**

```yaml
record:
  kind: "A"
  name: "core"
  address: "10.20.0.2"
```

**SOA record:**

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

### Example

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

### Notes

- The `domain` field must reference the parent domain's UUID via a link.
- The `address` field can be a link to a variable or a node's IP, enabling dynamic DNS updates when
  infrastructure changes.

---

## $core.network.lb

A load balancer. The platform provisions an nginx-based load balancer VM and configures it based on the
associated backend pools, vhosts, and routes.

### Fields

| Field | Type | Description |
|---|---|---|
| `name` | string | Load balancer name. |
| `description` | string | Human-readable description. |
| `type` | object | LB type configuration (see below). |
| `project_id` | uuid | Project UUID. |

### LB type

```yaml
type:
  kind: "core"
  ram: 512          # RAM in MB for the LB VM
  cpu: 1            # CPU cores
  disk_size: 10     # Disk size in GB
  nodes_number: 1   # Number of LB nodes
```

If `type` is omitted, the defaults are used: `kind: core`, `ram: 512`, `cpu: 1`, `disk_size: 10`,
`nodes_number: 1`.

### Example

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

### Notes

- Backend pools, vhosts, and routes are defined as nested resources (see below).
- The load balancer's IP addresses are available via the `:ipsv4` parameter after provisioning.

---

## $core.network.lb.$name.backend_pools

A backend pool — a group of endpoints that traffic is distributed across. Defined as a nested resource
under a load balancer.

### Fields

| Field | Type | Description |
|---|---|---|
| `name` | string | Pool name. |
| `description` | string | Human-readable description. |
| `parent` | uuid | The parent load balancer UUID. |
| `endpoints` | array | List of endpoint objects (see below). |
| `balance` | string | Load balancing algorithm (default: `roundrobin`). |
| `project_id` | uuid | Project UUID. |

### Endpoints

```yaml
endpoints:
  - kind: host
    host: $core.compute.nodes.$app_node:default_network:ipv4
    port: 443
    weight: 1
```

### Example

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

### Notes

- The `host` field is typically a link to a node's IP address. When the node's IP changes, the backend
  pool is automatically updated.
- The `parent` field must reference the parent load balancer's UUID.

---

## $core.network.lb.$name.vhosts

A virtual host on a load balancer — defines how the LB handles traffic for a specific protocol and port.

### Fields

| Field | Type | Description |
|---|---|---|
| `name` | string | Vhost name. |
| `description` | string | Human-readable description. |
| `parent` | uuid | The parent load balancer UUID. |
| `domains` | array | List of domain names. |
| `protocol` | string | `http`, `https`, `tcp`, or `udp`. |
| `port` | integer | Port to listen on (default: 80). |
| `cert` | object | TLS certificate (for `https` only, see below). |
| `enabled` | boolean | Whether the vhost is active (default: true). |
| `project_id` | uuid | Project UUID. |

### Certificate

For HTTPS vhosts, provide a certificate inline or via a link:

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

### Example

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

### Notes

- Routes are defined as nested resources under a vhost (see below).
- For HTTP-to-HTTPS redirect, create an HTTP vhost on port 80 with a redirect route.

---

## $core.network.lb.$name.vhosts.$name.routes

A route within a vhost — defines what action to take when a request matches a condition.

### Fields

| Field | Type | Description |
|---|---|---|
| `name` | string | Route name. |
| `description` | string | Human-readable description. |
| `parent` | uuid | The parent vhost UUID. |
| `condition` | object | Match condition (see below). |
| `enabled` | boolean | Whether the route is active (default: true). |
| `project_id` | uuid | Project UUID. |

### Conditions

**Prefix match:**

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

**Redirect:**

```yaml
condition:
  kind: prefix
  value: /
  actions:
    - kind: redirect
      url: https://example.com
```

**Raw (L4, for TCP/UDP vhosts):**

```yaml
condition:
  kind: raw
  actions:
    - kind: backend
      pool: $core.network.lb.$example_lb.backend_pools.$udp_pool:uuid
```

### Example

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

### Notes

- The `parent` field must reference the parent vhost's UUID.
- For L4 (TCP/UDP) vhosts, use `condition.kind: raw` since there is no HTTP path to match.

---

## $core.secret.certificates

A TLS certificate managed by the platform. Provisioned automatically via ACME (Let's Encrypt with
DNS-01 challenge using the platform's CoreDNS). Only the `dns_core` method is currently supported.

### Fields

| Field | Type | Description |
|---|---|---|
| `name` | string | Certificate name. |
| `description` | string | Human-readable description. |
| `email` | string | Email for ACME registration. |
| `method` | object | Provisioning method (see below). |
| `domains` | array | List of domains (supports wildcards like `*.example.com`). |
| `expiration_threshold` | integer | Days before expiration to trigger renewal. |
| `overcome_threshold` | boolean | Internal field populated from agent state; not settable via the user API. |
| `constructor` | object | How the cert/key are stored (default: `{"kind": "plain"}`). |
| `project_id` | uuid | Project UUID. |

### Method

```yaml
method:
  kind: dns_core    # ACME via DNS-01 challenge using CoreDNS
```

### Example

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

### Notes

- The `dns_core` method uses the platform's built-in CoreDNS to fulfill ACME DNS-01 challenges
  automatically. The domain must be managed by the platform's DNS.
- After provisioning, the certificate and key are stored on the target node's agent.
- Use `expiration_threshold` to control automatic renewal. The platform renews certificates before they
  expire.

---

## $core.secret.passwords

A password managed by the platform. Supports automatic generation (random hex, URL-safe base64) or manual
values.

### Fields

| Field | Type | Description |
|---|---|---|
| `name` | string | Password name. |
| `description` | string | Human-readable description. |
| `method` | string | `AUTO_HEX`, `AUTO_URL_SAFE`, or `MANUAL`. |
| `value` | string | The password value. Required for `MANUAL`; must be omitted for auto methods (setting it raises an error). |
| `default_length` | integer | Length for auto-generated passwords (default: 32). |
| `constructor` | object | How the password is stored (default: `{"kind": "plain"}`). |
| `project_id` | uuid | Project UUID. |

### Methods

| Method | Description |
|---|---|
| `AUTO_HEX` | Random hex string via `secrets.token_hex()`. Example: `a1b2c3d4e5f6...` |
| `AUTO_URL_SAFE` | Random URL-safe base64 via `secrets.token_urlsafe()`. Example: `a1b2c3d4-e5f6_...` |
| `MANUAL` | Use the exact value provided in the `value` field. |

### Example

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

### Notes

- Auto-generated passwords are created once at install time. The value is available via `:value` link
  parameter. With the default `constructor.kind: plain`, the password is stored as-is; configure a
  different constructor for at-rest protection.
- Use `AUTO_URL_SAFE` for passwords that may appear in URLs (API keys, tokens).

---

## $core.iam.organizations

An IAM organization — the top-level entity in the platform's identity and access management.

### Fields

| Field | Type | Description |
|---|---|---|
| `name` | string | Organization name. |
| `description` | string | Human-readable description. |
| `info` | object | Arbitrary metadata. |

### Example

```yaml
resources:
  $core.iam.organizations:
    jdoe_corp:
      name: "jdoe-corp"
      description: "John Corporation"
```

### Notes

- Organizations contain projects and members. Members are defined as nested resources (see below).
- An organization is required before creating projects.

---

## $core.iam.organizations.$name.members

A member of an organization. Defines a user's role within the organization.

### Fields

| Field | Type | Description |
|---|---|---|
| `organization` | uuid | The parent organization UUID. |
| `user` | uuid | The user UUID. |
| `role` | string | `OWNER` or `MEMBER`. |

### Example

```yaml
resources:
  $core.iam.organizations.$jdoe_corp.members:
    jdoe_member:
      organization: "$core.iam.organizations.$jdoe_corp:uuid"
      user: $core.iam.users.$jdoe:uuid
      role: "OWNER"
```

### Notes

- The `organization` and `user` fields are typically links to the respective resource UUIDs.
- An `OWNER` can manage the organization's members and projects; a `MEMBER` can only access projects
  they are assigned to.

---

## $core.iam.projects

An IAM project — a logical container for resources. Most resources require a `project_id` field.

### Fields

| Field | Type | Description |
|---|---|---|
| `name` | string | Project name. |
| `description` | string | Human-readable description. |
| `organization` | uuid | The parent organization UUID. |

### Example

```yaml
resources:
  $core.iam.projects:
    jdoe_pr:
      name: "jdoe-pr"
      description: "John environment"
      organization: "$core.iam.organizations.$jdoe_corp:uuid"
```

### Notes

- The `organization` field must reference an existing organization's UUID.
- The project's UUID is used as the `project_id` field in most other resource types.

---

## $core.iam.users

An IAM user account.

### Fields

| Field | Type | Description |
|---|---|---|
| `username` | string | Unique username. **Required.** |
| `email` | string | Email address. **Required.** |
| `password` | string | Initial password. |
| `first_name` | string | First name. |
| `last_name` | string | Last name. |
| `description` | string | Human-readable description. |
| `phone` | string | Phone number. |
| `type` | string | `user` (default), `service`, or `anon`. |
| `email_verified` | boolean | Whether the email is verified. |
| `otp_enabled` | boolean | Whether OTP is enabled. |

### Example

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

### Notes

- `service` type users are for machine-to-machine authentication (API clients, CI/CD pipelines).
- The `password` field sets the initial password. Users can change it later via the platform UI or API.

---

## $core.iam.role_bindings

Binds a user to a role, optionally scoped to a project.

### Fields

| Field | Type | Description |
|---|---|---|
| `role` | uuid | The role UUID. |
| `user` | uuid | The user UUID. |
| `project` | uuid | Optional. If set, the binding is scoped to this project. |

### Example

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

### Notes

- A binding without a `project` field is a global binding — the user has the role across all projects.
- A binding with a `project` field is scoped — the user has the role only within that project.
- Role UUIDs are predefined by the platform. Known built-in roles include `newcomer`
  (`726f6c65-0000-0000-0000-000000000001`, default for newly registered users) and `owner`
  (`726f6c65-0000-0000-0000-000000000002`, full administrative privileges within a project, assigned
  automatically on project creation). See the [IAM Permissions](../iam/permissions_overview.md) page for
  the role/permission model.

---

## See also

- [Manifest Guide](manifest.md) — manifest structure, links, lifecycle
- [Export/Import Guide](../core-developer-guide/exports.md) — sharing resources between elements
- [Core Developer Guide](../core-developer-guide/core-guide.md) — internal architecture
- [OpenAPI specification](../openapi/openapi_user.md) — field-level API details
