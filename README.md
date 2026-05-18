![Tests workflow](https://github.com/infraguys/exordos_core/actions/workflows/tests.yml/badge.svg)
![Build workflow](https://github.com/infraguys/exordos_core/actions/workflows/build.yml/badge.svg)
![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="logo_black.svg">
    <source media="(prefers-color-scheme: light)" srcset="logo_white.svg">
    <img height="256" src="logo_white.svg" alt="exordos core svg logo">
  </picture>
</p>

# Exordos Core

**📚 Documentation:** [exordos.github.io/exordos_core](https://exordos.github.io/exordos_core/)

Exordos Core is an open-source NoOps platform for managing corporate infrastructure and software ecosystems. It provides a unified platform layer — from bare metal and virtual machines all the way to applications and services — designed to be operated by both humans and AI agents.

## What Exordos Core does

Exordos Core is the next step beyond classic DevOps. Instead of a collection of disconnected tools requiring constant manual coordination, it gives your organization a single declarative platform layer where infrastructure, policies, and service lifecycle are managed as one coherent system.

Key capabilities:

- **Declarative management** — describe the desired state; the platform reconciles reality to match it automatically (self-healing). No more configuration drift or manual correction after every change.
- **Image-based provisioning** — fast, repeatable, and predictable delivery of environments and services. Rollouts and recovery become straightforward operations, not fire-fighting exercises.
- **AI-ready architecture** — the platform is designed from the ground up to be controlled by an AI agent, reducing manual toil and letting teams operate at the level of intent rather than low-level steps.
- **Unified lifecycle management** — infrastructure, internal services, and application lifecycle all managed through a single control plane. One system instead of a zoo of tools.
- **Sovereign deployment** — run entirely within your own on-prem or private cloud perimeter with no dependency on external services. Full control over data, access, and the operational boundary.
- **Element ecosystem** — install and manage ready-to-use software elements (databases, messengers, internal services, and more) directly from the platform marketplace, just like an app store for enterprise software.

> **For a full overview of architecture, configuration, and advanced usage, visit the [documentation](https://exordos.github.io/exordos_core/).**

# 🚀 To start using Exordos

Install the CLI tools with a single command:

```bash
curl -fsSL https://repo.exordos.com/install.sh | sudo sh
```

Then follow the instructions in the terminal, or refer to the [documentation](https://exordos.github.io/exordos_core/) for a full setup guide.

# 💡 Contributing

Contributing to the project is highly appreciated! However, some rules should be followed for successful inclusion of new changes in the project:

- All changes should be done in a separate branch.
- Changes should include not only new functionality or bug fixes, but also tests for the new code.
- After the changes are completed and **tested**, a Pull Request should be created with a clear description of the new functionality. And add one of the project maintainers as a reviewer.
- Changes can be merged only after receiving an approve from one of the project maintainers.
