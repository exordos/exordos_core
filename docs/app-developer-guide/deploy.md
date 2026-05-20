---
title: exordos elements install
---

## Overview

`exordos elements install` **installs** an element to a Exordos Core platform realm with necessary dependencies.

```bash
exordos elements install [OPTIONS] PATH_OR_NAME
```

Key options:

| Option | Description |
|---|---|
| `-r, --repository TEXT` | Repository endpoint where the element is stored [default: `https://repo.exordos.com/exordos-elements/`] |
| `PATH_OR_NAME` | Path to a local manifest file or the name of an element in the repository |

You can install elements on a [public Exordos Core installation](public-installation.md). Or you can deploy a [private Exordos Core installation](private-installation.md) on your own hardware. For development and testing, you can use [local laptop installation](local-laptop-installation.md).

---

## Getting Started

### Install from Repository

To install an element by name from the default repository:

```bash
exordos elements install my-element
```

The command fetches the element manifest from the repository, resolves dependencies, and provisions the element in the current realm.

### Install from Local Manifest

To install from a local manifest file:

```bash
exordos elements install ./manifests/my-element.yaml
```

### Install from Custom Repository

To install from a different repository endpoint:

```bash
exordos elements install -r https://my-repo.example.com/elements/ my-element
```

---

## Configuration

### Target Realm

The command deploys to the Exordos Core realm currently configured in your environment. Ensure you have:

- Valid credentials for the target realm
- Network connectivity to the realm's API endpoint
- Sufficient permissions to install elements

### Repository Selection

By default, elements are fetched from the public Exordos repository. For private or internal elements, specify a custom repository URL with the `-r` option.

---

## Next Steps

After a successful installation:

- Verify the element status in the Exordos Core CLI
- [Customize the manifest](../misc/manifests.md) to tailor the element configuration to your needs
- [Update the element](../admin-guide/index.md) when a new version is available

---

## Troubleshooting

If you encounter issues during deployment — such as element installation failures, dependency resolution errors, or realm connectivity problems — refer to the [Troubleshooting Guide](troubleshooting.md) for detailed solutions and common fixes.
