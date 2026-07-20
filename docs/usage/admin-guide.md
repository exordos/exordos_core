---
icon: lucide/settings
---
# Admin Guide

This guide is for administrators of a local Exordos Core installation. It covers host preparation, platform deployment, access configuration, and basic operating tasks.

## Scope

The guide describes a local installation on a single Ubuntu Linux 24.04 or 26.04 host using QEMU/KVM and libvirt. This environment is intended for development, testing, and functional verification of the platform.

## Prerequisites

The deployment requires:

- a host machine running Ubuntu Linux 24.04 or 26.04;
- permissions to install system packages and configure virtualization: QEMU version 8.2 or later and libvirt version 10.0 or later;
- access to the Exordos repository to install the CLI and download an image;
- an administrator SSH key for access to the primary node;
- a host machine with at least 4 CPU cores, 8 GB of RAM, and 50 GB of free disk space for the primary virtual machine and managed Nodes.

## Install Exordos CLI

Install Exordos CLI:

```bash
curl -fsSL https://repo.exordos.com/install.sh | sh
```

Review available commands and bootstrap options:

```bash
exordos --help
exordos bootstrap --help
```

## Prepare the host

Install QEMU/KVM version 8.2 or later, libvirt version 10.0 or later, and image utilities:

```bash
sudo apt update
sudo apt install qemu-kvm qemu-utils libvirt-daemon-system libvirt-dev mkisofs -y
```

Add the user that runs the CLI to the `libvirt` and `kvm` groups:

```bash
sudo adduser $USER libvirt
sudo adduser $USER kvm
```

Log out and log in again after changing group membership.

Initialize the host as a hypervisor with superuser privileges:

```bash
sudo exordos compute hypervisors init
```

Review initialization options:

```bash
exordos compute hypervisors init --help
```

## Deploy the platform

Bootstrap Exordos Core:

```bash
exordos bootstrap -i <version-or-image> -f -m core --ssh-public-key /path/to/public/key
```

The `<version-or-image>` argument accepts a path to a local image or a platform version from the repository. Examples:

```bash
exordos bootstrap -i /path/to/exordos-core.raw -m core
```

```bash
exordos bootstrap -i <version> -m core
```

For example, to install version `0.2.1`:

```bash
exordos bootstrap -i 0.2.1 -m core
```

Key bootstrap options:

| Option | Description |
| --- | --- |
| `--profile` | Installation profile: `develop`, `small`, `medium`, `large`, or `legacy`. |
| `--cidr` | Platform primary network CIDR. Default: `10.20.0.0/22`. |
| `--core-ip` | Primary virtual machine IP address. By default, the second address from `--cidr` is used. |
| `--admin-password` | Administrator password for the HTTP API. Generated automatically when omitted. |
| `--save-admin-password-file` | Saves the HTTP API administrator password to a file. |
| `--ssh-public-key` | Path to a public SSH key for access to the primary virtual machine. |
| `--hyper-connection-uri` | URI for a remote libvirt hypervisor connection. |
| `--hyper-storage-pool` | libvirt storage pool for virtual machine disks. |

After `bootstrap` completes successfully, the platform creates and starts the primary virtual machine, and the command prints the administrator login and password for the HTTP API. When `--save-admin-password-file` is set, the API password is saved to the specified file. On an error, the command prints an error message and exits with a non-zero status.

Store generated API administrator credentials in a secure location. Do not expose passwords in logs, manifests, or source-control systems.

### API administrator credentials

`bootstrap` creates a separate administrator login and password for each local installation to access the HTTP API. There are no permanent shared credentials. Use the login and password printed by `bootstrap` or saved using `--save-admin-password-file`.

Access to the primary virtual machine uses the SSH key whose public part was supplied with the `--ssh-public-key` bootstrap option; API login and password are not used for SSH.

For a reproducible deployment, provide the API password explicitly:

```bash
exordos bootstrap -i <version> -m core --admin-password <admin-password>
```

Do not publish actual installation API credentials in public documentation.

## Configure access

### SSH access

Connect to the primary virtual machine. Use the private part of the SSH key whose public part was supplied with the `--ssh-public-key` bootstrap option:

```bash
ssh ubuntu@<core-ip>
```

For the default bootstrap network `10.20.0.0/22`, the primary virtual machine address is `10.20.0.2`. The HTTP API administrator login and password are not used for SSH.

### Configure CLI

Register the platform endpoint and administrator context. For `<ADMIN_USERNAME>` and `<ADMIN_PASSWORD>`, use the HTTP API credentials created by `bootstrap`:

```bash
exordos settings set-realm local --endpoint http://<core-ip>:11010 --current
exordos settings set-context local --name admin -u <ADMIN_USERNAME> -p <ADMIN_PASSWORD> --current
```

Verify the connection:

```bash
exordos compute hypervisors list
exordos elements list
```

### HTTP API access

Use the API administrator login and password created by `bootstrap` to obtain an `access_token` from IAM before calling the HTTP API. See [Local Deployment](local_deployment.md) for a complete request example. Send the resulting token as a Bearer token in subsequent requests.

## Verify startup and command responses

After configuring the CLI, perform the following checks in order:

| Command | Purpose and expected result |
| --- | --- |
| `exordos realms list` | Lists local realms. The `local` realm must be present. |
| `exordos compute hypervisors list` | Lists registered hypervisors. The prepared hypervisor must be present. |
| `exordos compute nodes list` | Lists Nodes. After bootstrap, the platform primary Node is available. |
| `exordos elements list` | Lists installed elements. |
| `exordos em resources list` | Lists element resources and their states. |

The `exordos elements list` and `exordos em resources list` commands support `--filters key=value`, `--fields <field>`, `--output table|json|html|yaml`, `--watch`, and `--interval <seconds>`. The default output format is a table. With `--watch`, the CLI repeatedly requests data at the specified interval.

## Basic operations

### Monitor resources

Use the following commands to inspect compute resources:

```bash
exordos compute hypervisors list
exordos compute nodes list
```

Inspect element resources with:

```bash
exordos em resources list
exordos em resources show <uuid>
```

A resource progresses through `NEW → IN_PROGRESS → ACTIVE`. The `IN_PROGRESS` state means that the platform is still reconciling the resource to its desired state.

### Manage elements

List installed elements:

```bash
exordos elements list
```

Install an element:

```bash
exordos elements install <element-name>
```

See the [Manifest Reference](../em/manifest.md) for the element manifest and resource model.

### Configurations and secrets

Deliver configuration and confidential data with Config and Secret resources. Do not store passwords, keys, or certificates in plain text in manifests, command logs, or source repositories.

## Troubleshooting

If a resource does not leave `IN_PROGRESS`, inspect the resource, Node, and hypervisor first:

```bash
exordos em resources show <uuid>
exordos compute nodes list
exordos compute hypervisors list
```

See [Troubleshooting](troubleshooting.md) for common resource, configuration, and bootstrap script issues.

## Terminate the installation

To terminate a local installation in a controlled way, delete its local realm:

```bash
exordos realms delete local
```

The command deletes the local realm and terminates the local instance. Use it only to finish a test or expert environment. To start a new instance, create the installation again with `exordos bootstrap`.

## Updating

Updates are performed per element. Exordos Core is also an element and is updated with the standard command:

```bash
exordos elements update core
```

Before updating, retain the installed element version, network configuration, and access credentials. Test the update procedure in a separate local installation and verify the rollback strategy before updating a production environment.

## Related documentation

- [Platform Overview](platform-overview.md)
- [Local Deployment](local_deployment.md)
- [Troubleshooting](troubleshooting.md)
- [Support, Updates, and Product Development](support-lifecycle.md)
- [Security Guide](security.md)
- [Manifest Reference](../em/manifest.md)
