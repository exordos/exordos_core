---
icon: lucide/life-buoy
---
# Support, Updates, and Product Development

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

This page describes how users receive help while operating Exordos, obtain updates, and contribute to product development.

## Contact Channels

- [GitHub Issues](https://github.com/exordos/exordos_core/issues) for defect reports, improvement suggestions, and feature requests.
- [support@exordos.com](mailto:support@exordos.com) for installation, configuration, and platform operation consultations, as well as requests that must not disclose information in a public issue.

When submitting a request, provide the Exordos version, operating system, command executed, complete error text, and the steps that reproduce the problem. Do not provide passwords, private keys, tokens, or other secrets.

## Technical Support

Support helps users install and configure the platform, configure access, diagnose issues, and apply updates. Begin self-service diagnosis with the [Administrator Guide](admin-guide.md) and [Troubleshooting](troubleshooting.md).

Request handling follows these steps:

1. The user submits a request through GitHub Issues or email.
2. The request is analyzed; additional information to reproduce the issue may be requested.
3. The user receives configuration, operation, or remediation guidance.
4. The request is closed after the issue is resolved or an answer is provided.

## Issue Resolution

For known issues, see [Troubleshooting](troubleshooting.md). It includes checks for resource, Node, and hypervisor states, as well as steps for common configuration and bootstrap script errors.

If the issue is not documented, create a GitHub Issue or send an email request. After a defect is confirmed, a fix is prepared in the project source code and included in a subsequent Exordos version.

### Defect lifecycle

A defect goes through the following stages from discovery to resolution:

1. **Report** — the user submits a GitHub Issue or email describing the problem.
2. **Triage** — maintainers reproduce the issue, confirm it, and label it by severity and component.
3. **Diagnosis** — the root cause is identified in the source code.
4. **Fix** — a change is prepared on a feature branch with a test that reproduces the original problem.
5. **Review** — the change goes through code review and the CI pipeline (lint, unit and functional tests, type checking).
6. **Release** — the approved change is merged into `master` and included in the next version; the version is tagged and published.
7. **Closure** — the original issue is closed with a reference to the fixing version.

## Development process

Exordos is developed on GitHub using a feature-branch workflow:

1. **Branch** — changes are made on a branch cut from `master` (for example `feat/<topic>` or `docs/<topic>`).
2. **CI** — every push triggers automated checks: `ruff` lint, unit and functional tests on Python 3.10, 3.12, and 3.14 against PostgreSQL, and a full platform build with functional verification on a real hypervisor.
3. **Pull request** — the branch is opened as a PR against `master`. At least one maintainer review is required before merge.
4. **Merge** — after approval and green CI, the PR is merged into `master`.
5. **Release** — `master` is tagged with a version number (for example `0.2.19`). The tag triggers the publish workflow, which builds the Python package, publishes it to PyPI, signs the artifacts with Sigstore, and creates a GitHub Release.

External contributions follow the same process: fork the repository, open a PR, and pass review and CI.

## Update delivery

New versions are delivered through the Exordos repository:

1. A version tag is pushed to the repository.
2. The publish workflow builds the package and publishes it to PyPI and the Exordos repository.
3. The platform image is built and pushed to the repository by the build workflow.
4. Administrators update their installation with `exordos elements update core`, which pulls the new version from the repository and applies it.

The release history with change descriptions is available on the [releases page](https://github.com/exordos/exordos_core/releases).

## Updates and Fixes

New versions can contain defect fixes, security improvements, and new capabilities. Available versions are published in the [Exordos releases](https://github.com/exordos/exordos_core/releases).

Before updating:

1. Review the changes in the target version.
2. Retain the installed element version, network configuration, and access credentials.
3. Test the update in a separate local installation.
4. Prepare a rollback strategy.

See the [Administrator Guide](admin-guide.md#updating) for detailed update requirements. Updates are performed per element. Exordos Core is also an element and is updated with the standard command:

```bash
exordos elements update core
```

## Product Development

Users can submit improvement suggestions, feature requests, and behavior-change proposals through GitHub Issues or [support@exordos.com](mailto:support@exordos.com). Proposals are assessed for technical feasibility, security, compatibility, and user needs.

Accepted changes are implemented in the source code, verified, and included in future releases. Released changes are documented in release materials.
