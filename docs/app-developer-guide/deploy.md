---
title: exordos elements install & exordos deploy
---

## Overview

There are two ways to install a built element into a Exordos Core realm:

- `exordos elements install` — installs an element from a repository or a local manifest file. Use this when the element is already built and pushed, or when installing from a manifest path.
- `exordos deploy` — builds, pushes (optional), and installs in one step. Use this to deploy a locally built element without a separate `exordos push`.

---

## exordos elements install

`exordos elements install` **installs** an element to a Exordos Core platform realm with necessary dependencies.

```bash
exordos elements install [OPTIONS] [UUID_OR_NAME_OR_PATH]
```

Key options:

| Option | Description |
|---|---|
| `-v, --version TEXT` | Version of the element to install |
| `-p, --project-id UUID` | Project UUID, required only if the upload repository doesn't exist yet |
| `--timeout FLOAT` | Seconds to wait for repository upload and element sync to complete (default: `600`) |
| `UUID_OR_NAME_OR_PATH` | Element UUID, element name in the repository, or path to a local manifest file |

If no argument is given, the command interactively lists elements available in the repository and asks you to pick one.

You can install elements on a [public Exordos Core installation](../usage/public-installation.md). Or you can deploy a [private Exordos Core installation](../usage/private-installation.md) on your own hardware. For development and testing, you can use [local laptop installation](../usage/local-laptop-installation.md).

### Install from Repository

To install an element by name from the default repository:

```bash
exordos elements install my-element
```

The command fetches the element manifest from the repository, resolves dependencies, and provisions the element in the current realm.

### Install a Specific Version

```bash
exordos elements install -v 1.2.3 my-element
```

### Install from Local Manifest

To install from a local manifest file, pass the path to the manifest:

```bash
exordos elements install ./manifests/my-element.yaml
```

The manifest is uploaded to a local upload repository on the platform, then installed from there.

### Install by UUID

```bash
exordos elements install 11111111-2222-3333-4444-555555555555
```

---

## exordos deploy

`exordos deploy` **deploys a built element to a realm in one step**. The element must already be built (`exordos build`). With no `--repository`, the local build output is served in-process and installed directly — no `exordos push` needed. With `--repository`, the build is pushed first (exactly like `exordos push`), then installed.

```bash
exordos deploy [OPTIONS]
```

Key options:

| Option | Description |
|---|---|
| `-e, --element-dir PATH` | Directory where element artifacts are stored (output of `exordos build`, default: `output/`) |
| `-t, --repository TEXT` | Repository name (key from the `repositories` section in `~/.exordos/exordosctl.yaml`). Selects push mode: push the build to this repository first, then install. If omitted, local mode is used (no push) |
| `--element TEXT` | Name of the element to deploy from the build inventory. If omitted and multiple elements are available, an interactive prompt is shown |
| `-r, --realm TEXT` | Name of the realm to deploy to. If omitted, the current realm from the configuration is used |
| `-p, --project-id UUID` | Project UUID, required only if the dev repository doesn't exist yet |
| `--dev-repo-name TEXT` | Name of the local dev repository used to publish deployed elements (default: `exordos-dev-repo`) |
| `--dev-repo-priority INTEGER` | Priority of the local dev repository, 0–4096 (default: `4096`) |
| `-f, --force` | Force push even if the element already exists (push mode only) |
| `--timeout FLOAT` | Seconds to wait for repository sync and element install to complete (default: `600`) |
| `-c, --exordosctl-cfg-file TEXT` | Name of the exordosctl configuration file |
| `--port INTEGER` | TCP port used by the in-process HTTP server in local mode (default: `33101`) |

### Deploy a Local Build (No Push)

After `exordos build`, deploy the build output directly without pushing to a remote repository:

```bash
exordos deploy
```

The command serves the `output/` directory over a temporary local HTTP server and installs the element from there.

### Deploy and Push

To push the build to a configured repository first, then install it:

```bash
exordos deploy --repository my-repo
```

The repository must be configured in `~/.exordos/exordosctl.yaml` under the `repositories` section (see `exordos settings repo add`).

### Deploy a Specific Element

If the build produced multiple elements, select one:

```bash
exordos deploy --element my-app
```

---

## Configuration

### Target Realm

Both commands deploy to the Exordos Core realm currently configured in your environment. Ensure you have:

- Valid credentials for the target realm
- Network connectivity to the realm's API endpoint
- Sufficient permissions to install elements

### Repository Selection

`exordos elements install` resolves elements from the repositories registered on the platform. To use a custom repository, register it with `exordos settings repo add` or via the platform API before installing.

`exordos deploy --repository` pushes to a repository entry defined in `~/.exordos/exordosctl.yaml` under the `repositories` section.

---

## Next Steps

After a successful installation:

- Verify the element status in the Exordos Core CLI
- [Customize the manifest](../em/manifest.md) to tailor the element configuration to your needs
- [Update the element](../usage/admin-guide.md) when a new version is available

---

## Troubleshooting

If you encounter issues during deployment — such as element installation failures, dependency resolution errors, or realm connectivity problems — refer to the [Troubleshooting Guide](../usage/troubleshooting.md) for detailed solutions and common fixes.
