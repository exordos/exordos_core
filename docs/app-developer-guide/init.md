---
title: exordos init
---

## Overview

`exordos init` **platformizes** an existing project — it prepares everything the Exordos Core platform needs to build, publish, and deploy it as an element.

Running the command performs the following steps:

1. **Detect the project type** — identifies the runtime and framework (Python, Node.js, generic, etc.) or asks you to choose one.
2. **Configure the manifest** — collects metadata and infrastructure requirements (databases, services, etc.) to produce a manifest file.
3. **Set up CI/CD** — generates pipeline configuration for your CI/CD system so builds and deployments are automated from day one.
4. **Print a summary** — reports everything that was created or modified so you know exactly what changed.

```bash
exordos init [OPTIONS]
```

Key options:

| Option | Description |
|---|---|
| `--project-dir PATH` | Target directory (default: current directory) |
| `--force` | Overwrite previously generated files |

All other parameters are collected interactively via the wizard.

---

## Interactive Mode

`exordos init` runs as an **interactive wizard** — it guides you through a series of questions and makes decisions based on your answers. You don't need to know the options upfront; the wizard adapts its questions depending on the project type you select.

[Learn more about the wizard →](wizard.md)

---

## Getting Started

### Supported Project Types

When the wizard asks **"Choose project type"**, pick the one that matches your stack:

| Type | Description |
|---|---|
| **Python** | Python application. Supports `pip` and `uv` package managers. Optionally installs PostgreSQL, Redis, and systemd services. |
| **Node.js** | Node.js application. Optionally installs Nginx, PM2, and Redis. |
| **Generic** | Any other runtime or language. Use this when none of the above fit — you'll configure build and deploy steps manually. |

If you are unsure, choose **Generic** and refine the configuration later.

---

### Manifest

During initialization the wizard generates a **manifest** — a YAML file that tells the platform what your element needs: its dependencies, infrastructure resources (databases, queues, etc.), and runtime configuration.

You will be asked to provide:

- A short description of the element (`--manifest-description`).
- The manifest constructor type — the template used to generate the manifest (`--manifest-constructor`).
- PostgreSQL settings if your project uses a database (`--enable-pgsql`, `--pgsql-*`).

The generated manifest is a starting point. You can edit it by hand at any time.

[Full manifest reference →](../em/manifest.md)

---

### CI/CD

`exordos init` can generate a ready-to-use CI/CD pipeline configuration (`--ci-cd`). Currently supported:

- **GitLab CI** — produces a `.gitlab-ci.yml` that runs `exordos build`, `exordos push`, and `exordos deploy` on every relevant event.
- **GitHub Actions** — produces a `.github/workflows/` directory with workflows for build, push, and deploy.

---

### Summary

After the wizard finishes, `exordos init` prints a **summary** of everything it created or modified — manifest files, CI/CD configuration, and any other generated artifacts. Review it to confirm the setup matches your expectations before moving on to `exordos build`.

---

## Troubleshooting

If you encounter issues during initialization — such as project type detection failures, manifest generation errors, or CI/CD configuration problems — refer to the [Troubleshooting Guide](troubleshooting.md) for detailed solutions and common fixes.
