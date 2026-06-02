---
title: Application Developer Guide
---

This guide walks you through everything you need to build, publish, and deploy **elements** — the name Exordos Core gives to applications — on the platform.

## Quick Start

### 1. Initialize your element

Set up an existing project as a Exordos Core element. The `exordos init` command launches an interactive wizard that generates all required configuration files — including manifests, and a `exordos.yaml` file that describes the build, publish, and deploy procedures.

```bash
exordos init
```

After the wizard completes, your project will contain necessary configuration files — including manifests — that describe your element to the platform.

[Learn more →](init.md)

---

### 2. Build your element

Compile and package your element along with all its artifacts. Exordos Core uses the build configuration defined in `exordos.yaml` to produce a distributable artifact.

```bash
exordos build
```

On success, the build output (container image, binary, or other artifact type) is stored locally and tagged with the current element version.

[Learn more →](build.md)

---

### 3. Publish your element

Push the built element to the Exordos ecosystem registry so it becomes available to other platform users.

```bash
exordos push
```

The element is versioned and pushed to the configured repository based on the metadata in `exordos.yaml`.

[Learn more →](push.md)

---

### 4. Deploy your element

Install and run your element on a Exordos Core platform installation.

```bash
exordos elements install <element-name>
```

The platform resolves dependencies, installs the target element, and starts it in the chosen realm.

[Learn more →](deploy.md)

---

## Walkthrough: Your First Element

Let's walk through platformizing a real FastAPI project from `https://github.com/infraguys/todo_application`.

This is a simple ToDo List API with PostgreSQL persistence. The goal here is not to explore the business logic, but to demonstrate how to take an existing application and platformize it using Exordos Core.

### Prerequisites

To follow this walkthrough, you need a running Exordos installation and the necessary tools.

**Exordos** — you can use either:

- A **public Exordos installation** — hosted at [exordos.com](https://exordos.com). No setup required; just create an account and start using the platform.
- A **private Exordos installation** — your own self-hosted instance. This document describes how to set one up: [Local Deployment](../usage/local_deployment.md).

Install the necessary tools below.

Install Exordos CLI:

```bash
curl -fsSL https://repo.exordos.com/install.sh | sudo sh
```

The Packer [version 1.9.2](https://hashicorp-releases.yandexcloud.net/packer/1.9.2/) or earlier due to licensing limitation. Download and place into `/usr/local/bin/` or any other directory in your `$PATH`.

The Qemu:

```bash
sudo apt update
sudo apt install qemu-kvm mkisofs
sudo adduser $USER kvm
```

You may need to relogin to apply the changes. Now you are ready to build your element.

### Initialize the project

Clone the project:

```bash
git clone https://github.com/infraguys/todo_application.git && cd todo_application
```

The `exordos init` command allows you to initialize a new element interactively but you can also use it with flags. We will use the flags approach here to speed things up.

```bash
exordos init \
  --project-name todo_application \
  --project-type python \
  --project-systemd-services "todo-api" \
  --project-url "https://github.com/infraguys/todo_application" \
  --project-python-package-manager "pip" \
  --enable-pgsql \
  --author-name "Developer" \
  --author-email "dev@example.com" \
  --manifest-description "A simple ToDo list element" \
  --repository "https://repo.exordos.com/exordos-elements" \
  --pgsql-usage-mode "own_cluster" \
  --pgsql-database-name "todo_api" \
  --pgsql-username "todo_api" \
  --ci-cd "none"
```

After executing this command, a summary will be displayed with the results of the initialization, showing the main configuration files. Study them to understand what was created.

The project is ready to be built and pushed as a Exordos Core element.

### Build the element

To build the element, run:

```bash
exordos build
```

### Push to repository

Use the repository of your exordos installation and push the element:

```bash
exordos push -c exordos.push.yaml
```

### Deploy the element

```bash
exordos elements install todo-api
```

The ToDo API is now available at the configured endpoint.

---

## Advanced Usage

Once you're comfortable with the basics, explore the more in-depth topics below.

- [Writing a manifest from scratch](../em/manifest.md) — understand the full manifest specification and author one by hand without relying on the `exordos init` wizard.
- [Setting up a private platform installation](../usage/local_deployment.md) — spin up your own Exordos Core instance to develop and test your elements end-to-end without connecting to a remote environment.
- [Public Exordos installation](https://exordos.com) — use the hosted Exordos platform without managing your own infrastructure.
