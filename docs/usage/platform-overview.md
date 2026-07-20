---
icon: lucide/map
---
# Exordos Core Overview

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

Exordos Core is an open-source NoOps platform for automating the deployment, management, and operation of IT infrastructure and enterprise software. The platform can be used in local, private, public, and hybrid environments.

Exordos Core moves infrastructure management beyond the classic DevOps approach. Instead of disconnected tools that require constant manual coordination, it unifies infrastructure, access policies, and service lifecycle in one control plane. This reduces operational overhead, prevents configuration drift, and lets teams work at the level of intent rather than individual low-level operations.

## Core principles

### Declarative management

A manifest describes resources, their parameters, dependencies, configuration, and lifecycle rules. The platform determines the operations required to reach the declared state.

### Reconciliation loop

Exordos Core continuously compares the actual state of managed resources with the state declared in the manifest. When it detects a difference, the platform performs the actions necessary to restore the desired state.

### Image-based provisioning

Compute nodes are created and updated from images. During an update, the Node root disk is replaced with the target image while attached volumes and network identities are preserved. This approach makes environments reproducible and reduces configuration drift between nodes.

### Elements

Software is delivered as elements. An element contains a manifest describing dependencies, infrastructure resources, configuration, secrets, and installation, update, and removal rules.

## Main components

- **Compute** — creates and manages virtual machines and physical servers.
- **Storage** — creates and attaches disk volumes.
- **Network** — manages network resources.
- **DNS** — manages DNS resources.
- **Load Balancer** — manages load balancers.
- **IAM** — manages users, roles, and access permissions.
- **Secrets** — stores passwords, SSH and RSA keys, certificates, and other confidential data.
- **Configs** — stores and delivers configuration data to software components.
- **Services** — manages system services on compute nodes.
- **Values Store** — manages values, variables, and profiles used to parameterize manifests.
- **Element Manager** — installs, updates, and manages the lifecycle of elements.
- **Marketplace** — provides public and private element catalogs.

## Core platform resources

The platform provides additional resource types; the following are its core resources.

| Resource | Purpose |
| --- | --- |
| Node | A virtual machine or physical server. A Node is not a container. |
| Set | A group of homogeneous Nodes with a specified replica count and update strategy. |
| Volume | An attachable disk volume for persistent data. |
| Network | A network resource connecting compute nodes and services. |
| Config | Configuration data delivered to components. |
| Secret | Confidential data such as passwords, keys, and certificates. |
| Service | A system service that runs on a Node. |
| Element | A package of application or infrastructure software. |

## Functional capabilities

Exordos Core provides:

- creation, configuration, and deletion of virtual machines and physical servers;
- Node Set creation for workload scaling;
- `in-place`, `replace`, and `rolling` Set update strategies with rollback;
- management of volumes, network resources, DNS, and load balancers;
- element installation, update, removal, and dependency resolution;
- centralized management of configuration, secrets, values, variables, and profiles;
- user, role, and access-permission management;
- creation, startup, shutdown, and update of system services;
- element discovery, installation, and distribution through public and private catalogs;
- integration with external systems through the platform services HTTP API.

## Next steps

- [Local Deployment](local_deployment.md)
- [Admin Guide](admin-guide.md)
- [Application Developer Guide](../app-developer-guide/app-guide.md)
- [Manifest Reference](../em/manifest.md)
