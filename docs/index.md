---
title: Exordos Core
---

Welcome to Exordos Core!

**Exordos** is an open-source NoOps platform for managing infrastructure at every level, along with the ecosystem built on top of it.

## 📦 Installation

### Linux

```bash
curl -fsSL https://repo.exordos.com/install.sh | sudo sh
```

## 🚀 Application Developer Guide

Everything you need to develop and deploy applications on the Exordos Core platform. In platform terminology, applications are called **elements**. The guide covers all stages of the element lifecycle:

- **Writing a manifest** — defining the element structure, its dependencies and configuration.
- **Building** — packaging the element and its artifacts.
- **Publishing** — releasing the element to the ecosystem registry.
- **Deploying** — installing and running the element on the platform.

[Go to the guide →](app-developer-guide/index.md)

## 🔧 Core Developer Guide

A guide for developers of the platform core and ecosystem elements — for example, a new PaaS service that other developers will use going forward.

[Go to the guide →](core-developer-guide/index.md)

## 🛠️ Admin Guide

Platform administrator documentation: managing the installation, configuring components, monitoring and maintaining the deployment.

[Go to the guide →](admin-guide/index.md)

## 🔒 Security Guide

Documentation for security engineers. Special focus on IAM and user management, system auditing, access policies, and other information security aspects of the platform.

[Go to the guide →](security-guide/index.md)

## 🌐 Ecosystem

## 📎 Miscellaneous

Additional reference materials for the platform:

- [Local Deployment](usage/local_deployment.md) — deploying a local installation of the platform on a single host.
- [Manifests](misc/manifests.md) — manifest format specification and examples.
- [API Reference](misc/api-reference.md) — full HTTP API reference for platform services.
- [CLI Reference](misc/cli-reference.md) — complete reference for the Exordos CLI tool.
