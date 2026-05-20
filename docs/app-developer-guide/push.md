---
title: exordos push
---

## Overview

`exordos push` **publishes** your built elements to a Exordos repository, making them available for deployment and for other platform users to consume.

The command reads the **`exordos/exordos.yaml`** file to identify where to push the elements. The `push` section defines one or more target repositories — local filesystem directories or remote HTTP endpoints.

```bash
exordos push [OPTIONS] [PROJECT_DIR]
```

Key options:

| Option | Description |
|---|---|
| `-c, --exordos-cfg-file TEXT` | Name of the project configuration file (default: `exordos.yaml`) |
| `-t, --target TEXT` | Target repository to push to (if multiple are defined) |
| `-e, --element-dir PATH` | Directory where element artifacts are stored (default: `output/`) |
| `-f, --force` | Force push even if the element already exists |
| `-l, --latest` | Also push the element as the `latest` version (for stable versions) |

You can push elements to the [public Exordos Core repository](public-installation.md). Or you can deploy a [private Exordos Core installation](private-installation.md) on your own hardware. For development and testing, you can use [local laptop installation](local-laptop-installation.md).

---

## Getting Started

### Basic Push

From your project directory after a successful build:

```bash
exordos push
```

The command reads `exordos.yaml`, finds the push configuration, and uploads the element artifacts from the `output/` directory to the configured repositories.

### Push to Specific Target

If your `exordos.yaml` defines multiple repositories, specify which one to push to:

```bash
exordos push --target local
```

### Force Push

Overwrite an existing element version:

```bash
exordos push --force
```

### Push as Latest

For stable releases, also tag the element as `latest`:

```bash
exordos push --latest
```

---

## Push Configuration

### Configuration in exordos.yaml

The `push` section in `exordos.yaml` defines where elements are published. You can configure multiple repositories:

```yaml
push:
  local:
    driver: fs
    path: /var/lib/exordos-pools/http
  remote:
    driver: nginx
    url: https://repo.exordos.com
```

| Repository Type | Description |
|---|---|
| `local` | Local filesystem directory. Elements are copied directly to the specified path. |
| `remote` | Remote HTTP repository. Elements are pushed via HTTP to the configured URL. |

### Driver Types

| Driver | Description |
|---|---|
| `fs` | Filesystem driver. Copies artifacts to a local directory path. |
| `nginx` | HTTP driver. Uploads artifacts to an HTTP/HTTPS endpoint, typically an Nginx-served repository. |

### Separate Configuration File

Push configuration can also be stored in a separate file, for example `exordos.push.yaml`:

```yaml
push:
  staging:
    driver: fs
    path: /var/lib/exordos-pools/staging
  production:
    driver: nginx
    url: https://repo.exordos.com/production
```

To use a separate configuration file, specify it with the `-c` option:

```bash
exordos push -c exordos.push.yaml
```

---

## Next Steps

After a successful push, your element is available in the registry and ready for:

- [`exordos deploy`](deploy.md) — deploy the element to a Exordos installation
- Sharing with other developers in your organization

---

## Troubleshooting

If you encounter issues while pushing elements — such as repository connection failures, authentication errors, or upload problems — refer to the [Troubleshooting Guide](troubleshooting.md) for detailed solutions and common fixes.
