---
icon: lucide/wand
---

## Overview

The `exordos init` wizard collects everything needed to platformize your project through a short series of interactive questions. It adapts the questions based on your answers, so you don't need to know every CLI option upfront.

See [`exordos init`](../app-developer-guide/init.md) for the command overview.

## Steps

1. **Choose project type** — Generic, Python, or Node.js (20/22/24).
2. **Configure the manifest** — description, element repository (where built images are published), and PostgreSQL settings if your project needs a database.
3. **Configure CI/CD** — GitLab CI, GitHub Actions, both, or none. Only GitLab CI generates files today; the GitHub options are accepted but produce no configuration yet.

### Python-specific prompts

- Package manager: `uv` or `pip`.
- The systemd services the project manages (comma-separated list).

### Node.js-specific prompts

- Whether to install Redis, Nginx, and PM2.
- The list of packages to install.
- The project user to run the application as.

Once all questions are answered, the wizard generates the manifest and CI/CD configuration, then prints a summary of what it created.

[Back to `exordos init` →](../app-developer-guide/init.md)
