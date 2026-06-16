---
title: exordos build
---

## Overview

`exordos build` **compiles and packages** your Exordos element into distributable artifacts — images, manifests, and any other resources required for deployment.

The command reads the **`exordos/exordos.yaml`** file — the main configuration file that transforms your application into a platform element. This file contains the `build` section describing:

- **Dependencies** — files, directories or external binary artifacts required for the build
- **Elements** — a single project can produce multiple elements on output (e.g., separate API and worker services)
- **Artifacts** — images, binaries, or other build outputs

Running the command performs the following steps:

1. **Resolve dependencies** — fetches dependencies specified in `exordos.yaml`.
2. **Process manifests** — renders manifest files (supporting raw YAML or Jinja2 templates with built-in variables).
3. **Build artifacts** — creates images and other build outputs based on the `build` section in `exordos.yaml`.

```bash
exordos build [OPTIONS] PROJECT_DIR
```

Key options:

| Option | Description |
|---|---|
| `-c, --exordos-cfg-file TEXT` | Name of the project configuration file (default: `exordos.yaml`) |
| `--build-dir TEXT` | Directory for temporary build artifacts |
| `--output-dir TEXT` | Directory where final artifacts are stored |
| `--deps-dir TEXT` | Directory where dependencies are fetched |
| `-i, --developer-key-path TEXT` | Path to developer's public key. The key is embedded into the built images for signing and authentication |
| `-f, --force` | Rebuild even if output already exists |

---

## Requirements

Before building, ensure you have the following tools installed:

- [packer](https://www.packer.io/)
- [qemu](https://www.qemu.org/)

Use the Packer [version 1.9.2](https://hashicorp-releases.yandexcloud.net/packer/1.9.2/) or earlier due to licensing limitation.

### Linux (Ubuntu)

Install packages

```bash
sudo apt update
sudo apt install qemu-kvm mkisofs
```

Add user to group

```bash
sudo adduser $USER kvm
```

You may need to relogin to apply the changes. Now you are ready to build your element.

## Getting Started

### Basic Build

From your project directory:

```bash
exordos build .
```

### Rebuild with Force

If you need to rebuild even when artifacts already exist:

```bash
exordos build --force .
```

### Build with Custom Variables

Pass additional variables to manifest templates:

```bash
exordos build --manifest-var commit_hash=$(git rev-parse --short HEAD) .
```

### Build only images

Build only images, skip manifests and other artifacts:

```bash
exordos build --only-images
```

---

## Build Process

The build process creates **VM disk images** that run on the Exordos Core platform. Packer starts a virtual machine from a base OS image, copies all resolved dependencies into it, and executes the provisioning script specified in `exordos.yaml`. This script is typically a Bash script that installs packages, configures services, and prepares the image. Once provisioning completes, the VM shuts down and the resulting disk image is packaged for deployment. This process is configured through the `elements` section in `exordos.yaml` where you define the base profile, provisioning script, output format, and any build overrides.

---

### Dependencies Resolution

Before building, `exordos build` resolves all dependencies declared in the `build.deps` section of `exordos.yaml`. Each dependency specifies a destination path (`dst`) and a source. Dependencies can be local directories, remote artifacts, or optional development resources.

#### Local Project Directory

Include another local project or directory into your build. The source path is relative to the `exordos.yaml` file location.

```yaml
deps:
  - dst: /opt/exordos_core
    path:
      src: ../../exordos_core
    exclude:
      - .venv
      - .tox
      - build
      - output
```

This copies the `exordos_core` project into `/opt/exordos_core` during the build, excluding development directories.

#### External Binary Artifacts

Fetch remote resources via HTTP/HTTPS. This is useful for kernel images, boot loaders, or pre-compiled binaries.

```yaml
deps:
  - dst: /opt/exordos_core/artifacts/vmlinuz
    http:
      src: https://repo.exordos.com/seed_os/1.1.0/vmlinuz
```

The `vmlinuz` kernel image is downloaded and placed at the specified destination path before the build continues.

#### Optional Dependencies

Mark dependencies as optional to allow builds to proceed even when the source is unavailable. This is useful for development-only resources.

```yaml
deps:
  - dst: /opt/gcl_sdk
    optional: true
    path:
      env: LOCAL_GENESIS_SDK_PATH
```

Here, the SDK is only included if the `LOCAL_GENESIS_SDK_PATH` environment variable is set. If not present, the build continues without this dependency.

---

### VM Image Build Configuration

Each element in the `elements` list of `exordos.yaml` defines how its VM images are built. An element can produce multiple images (for example, different formats or variants). The following parameters control the image creation process:

| Parameter | Description |
|---|---|
| `name` | The name of the output image. Used to identify the image in the build output and registry. |
| `format` | Disk image format. Supported formats: `raw`, `qcow2`, `gz`. The `gz` format is a compressed `raw` image. Can also reference an environment variable like `GEN_IMG_FORMAT_CORE=qcow2`. |
| `profile` | Base OS image profile to use as the starting point (e.g., `exordos_base`). This determines the initial operating system and pre-installed packages. |
| `script` | Path to the provisioning script executed inside the VM. This script performs all application-specific setup: installing dependencies, copying files, configuring services. |
| `override` | Build-time parameter overrides passed to the underlying tool (e.g., Packer). Common uses include increasing `disk_size`, `cpus`, or `memory` for the build VM. When using the `exordos_custom` profile, specify `base_image_url` and `base_image_checksum` here to use any custom image as the base. |
| `envs` | List of environment variables to pass into the build process. These become available to the provisioning script and build tools. |

#### Using Environment Variables

Define variables in `exordos.yaml`:

```yaml
elements:
  - manifest: manifests/core.yaml.j2
    images:
      - name: my-app
        format: qcow2
        profile: exordos_base
        script: install.sh
        envs:
          - APP_PORT=8080
          - LOG_LEVEL=info
          - DATABASE_URL
```

Use them in your provisioning script:

```bash
#!/bin/bash
# install.sh

echo "Starting application on port $APP_PORT"
echo "Log level set to: $LOG_LEVEL"

# DATABASE_URL will be passed if set in the environment
if [ -n "$DATABASE_URL" ]; then
    echo "Database configured: $DATABASE_URL"
fi
```

### Manifest Processing

The manifest in your project can be either:

- **Raw YAML** — used as-is during the build
- **Jinja2 template** — dynamically rendered with built-in variables

For Jinja2 templates, the following variables are available by default:

| Variable | Description |
|---|---|
| `{{ version }}` | Version of the element being built |
| `{{ name }}` | Name of the element |
| `{{ images }}` | List of images built for this element |
| `{{ manifests }}` | List of manifest files |

Additional variables can be passed using `--manifest-var key=value`:

```bash
exordos build --manifest-var environment=production --manifest-var region=europe-east .
```

[Full manifest reference →](../em/manifest.md)

---

### Output Artifacts

After a successful build the following artifacts are created in the `--output-dir` (default: project `output/`):

```text
output/
├── inventory.json          # Build manifest listing all produced artifacts
├── images/                 # Built VM disk images
│   └── <element-name>.<format>
└── manifests/              # Compiled manifests for each element
    └── <element-name>.yaml
```

The `inventory.json` file provides a complete index of the build output, mapping each element to its artifacts:

| Field | Description |
|---|---|
| `name` | Element name |
| `version` | Full version string with timestamp and git hash |
| `images` | List of built VM disk image paths |
| `manifests` | List of compiled manifest paths |
| `artifacts` | Additional build artifacts (if any) |
| `configs` | Configuration files (if any) |
| `templates` | Template files used during build (if any) |

The exact contents depend on your project type and `exordos.yaml` configuration.

---

## Next Steps

After a successful build, your elements are ready for:

- [`exordos push`](push.md) — publish to the ecosystem registry
- [`exordos deploy`](deploy.md) — deploy to a Exordos installation

---

## Troubleshooting

If you encounter issues during the build process — such as dependency resolution failures, VM image build errors, or manifest processing problems — refer to the [Troubleshooting Guide](../usage/troubleshooting.md) for detailed solutions and common fixes.
