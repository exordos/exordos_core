---
icon: lucide/clipboard-list
---
# Functional Characteristics of the Software Instance

<!--
Copyright 2026 Genesis Corporation JSC

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

Exordos Core is an open-source software platform (NoOps) for automating the deployment, management, and operation of IT infrastructure and enterprise software. The platform is designed for use in local, private, public, and hybrid environments.

## 1. Purpose and scope

Scope of application:

- Automation of deployment and management of virtual machines and physical servers.
- Declarative management of configurations, secrets, variables, and profiles.
- Lifecycle management of elements (applications and infrastructure components).
- Access and user permission management (IAM).
- Load balancing and DNS management.
- Deployment and update of applications in an image-based environment.

## 2. Functional modules

The platform includes the following functional modules.

### 2.1. Compute module

Provides creation and management of virtual machines (KVM/QEMU) and physical servers.

- Creation, configuration, and deletion of virtual machines (Node).
- Grouping of nodes into NodeSet with a target replica count (`replicas`).
- Image-based provisioning: compute nodes are created and updated from images; during an update, the root disk is replaced with the target image while attached volumes and network identities are preserved.
- Management of hypervisors (Hypervisor) and storage pools.
- Node placement policies (PlacementPolicy).

### 2.2. Storage module

Provides creation and attachment of disk volumes (Volume) for persistent data storage.

- Creation of volumes with a specified size and type.
- Support for multiple disks per node (root disk + data disks).
- Attachment and detachment of volumes to compute nodes.

### 2.3. Network module

Provides management of network resources.

- Creation and configuration of network resources.
- Management of node network connections.
- Support for routing and project network isolation.

### 2.4. DNS module

Provides management of DNS resources through a built-in DNS server based on CoreDNS.

- Creation and management of DNS domains.
- Creation of DNS records of types A, NS, SOA, TXT.
- Synchronization of domains with external DNS (ecosystem DNS).

The realm mirror writes `realm:<realm_uuid>` into each upstream record's tags and
updates or removes only records carrying its own mark. Local `realm:` tags are
replaced with the current realm's mark. Other realms' and unmarked records remain
intact, including records with a conflicting UUID.
The mark is a reconciliation label; IAM and project permissions still authorize
API access.

Before handing legacy records to the mirror, verify their origin and add the
realm mark through the upstream DNS API, preserving their other tags. A UUID or
matching content alone does not establish ownership. Older upstream APIs without
tag support accept new unmarked records, but existing records are not updated or
removed until the upstream is upgraded and those records are explicitly marked.
When tag filtering is unavailable, the mirror reads the whole zone and still
checks record ownership if the response supports tags. Filter refusal alone does
not disable marking, updating, or cleanup.
The mirror retries an unsupported tag filter after one hour; unrelated server
errors remain failures. Record listing supports `q=tags:"realm:<realm_uuid>"`;
unknown field parameters and invalid typed values return HTTP 400. A bare `*`
record name denotes the zone's wildcard.

### 2.5. Load Balancer module

Provides nginx-based load balancing.

- Creation and management of load balancers (LB).
- Support for virtual hosts (Vhost) with HTTP, HTTPS, TCP, UDP protocols.
- Backend pools with the `roundrobin` balancing algorithm.
- Prefix-based and L4 (raw) routing.
- Support for TLS termination (HTTPS).

### 2.6. IAM module (Access management)

Provides management of users, roles, and access permissions.

- "Deny by default" model — access is explicitly granted through permissions.
- Management of users (User), organizations (Organization), projects (Project).
- Roles (Role) and permission bindings (Permission Binding, Role Binding).
- Global and project-scoped role bindings.
- Token introspection for access permission verification.
- Support for wildcard permissions (`*.*.*`).
- User types: `user`, `service`, `anon`.

### 2.7. Secrets module

Provides storage and management of confidential data.

- Password management: automatic generation (`AUTO_HEX`, `AUTO_URL_SAFE`) and manual values (`MANUAL`).
- TLS certificate management: automatic issuance through ACME (Let's Encrypt) with DNS-01 verification via the built-in CoreDNS.
- Management of SSH keys and RSA keys.

### 2.8. Configs module

Provides delivery of configuration data to compute nodes.

- Creation of configuration files with text content.
- Delivery of files to target nodes with specified path, owner, and access permissions.
- Automatic configuration update when related values change (link tracking).
- Actions on change: `no_action`, `shell` (e.g., service restart).

### 2.9. Services module

Provides management of system services on compute nodes.

- Creation and management of systemd services on nodes and NodeSet.
- Service types: `simple`, `oneshot`, `monopoly`, `monopoly_oneshot`.
- Lifecycle management: start, stop, update.
- Dependencies between services (After, Before).

### 2.10. Values Store module

Provides management of variables, values, and profiles.

- Variables (Variable) with profile binding (`develop`, `small`, `medium`, `large`, `legacy`).
- Selector variables with value binding support.
- Values (Value) — concrete data bound to variables.
- Profiles (Profile) — sets of values for different deployment scales.

### 2.11. Element Manager module

Provides installation, update, and lifecycle management of elements.

- Installation of elements from a manifest (YAML).
- Dependency resolution between elements (requirements).
- Import and export of resources between elements.
- Update of elements with state preservation.
- Deletion of elements with dependency checking.
- Continuous reconciliation of declared and actual state (reconciliation loop).

### 2.12. Marketplace module

Provides public and private element catalogs.

- Discovery and installation of elements from catalogs.
- Public catalogs for element distribution.
- Private catalogs for corporate elements.

## 3. Technical characteristics of the instance

### 3.1. System requirements (deployment host)

| Parameter | Minimum | Recommended |
| --- | --- | --- |
| OS | Ubuntu 24.04 / 26.04 | Ubuntu 26.04 |
| Architecture | x86_64 | x86_64 |
| CPU | 4 cores | 8+ cores |
| RAM | 8 GB | 16 GB |
| Disk (free space) | 50 GB | 100+ GB |
| Hypervisor | QEMU 8.2+, libvirt 10.0+ | QEMU 9.0+, libvirt 10.0+ |

### 3.2. Managed virtual machine characteristics

| Parameter | Range |
| --- | --- |
| CPU per VM | from 1 to 32+ cores |
| RAM per VM | from 512 MB to 512+ GB |
| Root disk size | from 1 GB |
| Data disk size | from 1 GB |
| Node type | VM (KVM/QEMU) or HW (bare metal) |
| Maximum number of nodes | limited by hypervisor resources |
| Maximum number of NodeSets | limited by hypervisor resources |

### 3.3. Network characteristics

| Parameter | Value |
| --- | --- |
| Platform network | `10.20.0.0/22` (default, configurable) |
| HTTP API protocols | HTTP/HTTPS |
| HTTP API authentication | Bearer token (JWT) via IAM |
| SSH access to VM | by public key |

### 3.4. Host software requirements

| Component | Version |
| --- | --- |
| QEMU | >= 8.2 |
| libvirt | >= 10.0 |
| curl (for CLI) | any modern |
| Python (for agents) | >= 3.10 |
| systemd | any modern |

## 4. Functional capabilities (full list)

1. Creation, configuration, and deletion of virtual machines and physical servers.
2. NodeSet creation for workload scaling with a target replica count.
3. Volume management: creation, attachment, detachment.
4. Management of network resources and connections.
5. Management of DNS domains and records (A, NS, SOA, TXT).
6. Management of load balancers (nginx-based).
7. HTTP/HTTPS/TCP/UDP traffic balancing.
8. Installation of elements (applications) from manifests with dependency resolution.
9. Update and deletion of elements with dependency checking.
10. Import and export of resources between elements.
11. Centralized management of configuration files with delivery to nodes.
12. Centralized management of secrets: passwords, certificates, keys.
13. Automatic password generation (`AUTO_HEX`, `AUTO_URL_SAFE`).
14. Automatic TLS certificate issuance through ACME (Let's Encrypt) with DNS-01.
15. Management of variables, values, and profiles (Values Store).
16. Scaling profiles: `develop`, `small`, `medium`, `large`, `legacy`.
17. User, role, and access permission management (IAM).
18. "Deny by default" security model.
19. Global and project-scoped role bindings.
20. Management of services (systemd) on nodes: creation, start, stop, update.
21. Continuous reconciliation of declared and actual state (reconciliation loop).
22. Tracking of linked fields (links) and automatic update of dependent resources.
23. Image-based provisioning of compute nodes with root disk replacement.
24. Access to the platform HTTP API for integration with external systems.
25. CLI for platform management (`exordos` CLI).
26. Discovery and installation of elements from public and private catalogs.

## 5. API and integration

The platform provides an HTTP REST API for managing all resources.

| Resource | API Endpoint |
| --- | --- |
| MachinePool (Hypervisor) | `GET/POST /v1/compute/hypervisors/` |
| Node (VM) | `GET/POST /v1/compute/nodes/` |
| NodeSet | `GET/POST /v1/compute/sets/` |
| Volume | `GET/POST /v1/compute/volumes/` |
| Config | `GET/POST /v1/config/configs/` |
| Domain | `GET/POST /v1/dns/domains/` |
| LB | `GET/POST /v1/network/lb/` |
| Service | `GET/POST /v1/em/services/` |
| Certificate | `GET/POST /v1/secret/certificates/` |
| Password | `GET/POST /v1/secret/passwords/` |
| User | `GET/POST /v1/iam/users/` |
| Organization | `GET/POST /v1/iam/organizations/` |
| Project | `GET/POST /v1/iam/projects/` |
| Role | `GET/POST /v1/iam/roles/` |
| Permission | `GET/POST /v1/iam/permissions/` |
| Profile | `GET/POST /v1/vs/profiles/` |
| Variable | `GET/POST /v1/vs/variables/` |

The full API specification is available in the documentation in OpenAPI (Swagger) format.

## 6. Licensing and distribution

- License: Apache License 2.0 (open-source software).
- Distribution: through the public Exordos repository (`repo.exordos.com`) and the element catalog.
- Support: through GitHub Issues and email ([support@exordos.com](mailto:support@exordos.com)).
