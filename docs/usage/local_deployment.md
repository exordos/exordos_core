---
icon: lucide/hard-drive-download
---
# Local Deployment

This guide describes how to deploy a local installation of the Exordos platform on a single host machine.

## Dependencies

It's assumed Linux (Ubuntu) is used as the OS on your machine.

### Exordos CLI

Install the Exordos CLI:

```bash
curl -fsSL https://repo.exordos.com/install.sh | sh
```

### Packages

Install necessary packages:

```bash
sudo apt update
sudo apt install qemu-kvm qemu-utils libvirt-daemon-system libvirt-dev mkisofs -y
```

Add the current user to the required groups:

```bash
sudo adduser $USER libvirt
sudo adduser $USER kvm
```

## Local machine as a hypervisor

The local host must be configured as a hypervisor so that the platform can schedule and run virtual machine workloads on it.

Initialize the current host as a hypervisor:

```bash
exordos compute hypervisors init
```

### Key parameters

Run `exordos compute hypervisors init --help` to see all available options. The most important ones are:

| Option | Description |
|---|---|
| `--pool-name TEXT` | Name of the libvirt storage pool to use for VM disk images. Defaults to `default`. |
| `--packer` / `-p` | Install HashiCorp Packer alongside the hypervisor setup. |
| `--romfile-version TEXT` | Version of the network interface ROM file to install. Defaults to `latest`. |

## Bootstrap

Once the local machine is configured as a hypervisor, run the bootstrap procedure to deploy the platform:

```bash
exordos bootstrap -i <version> -f -m core --ssh-public-key /path/to/public/key
```

where `<version>` is the version of the platform to deploy (e.g., `0.0.6`). Available versions can be found on the [releases page](https://github.com/exordos/exordos_core/releases).

The platform can be started either from a **local build** (a locally built image) or from a **remote repository** (a prebuilt image fetched from the official repository).

**Local build example:**

```bash
exordos bootstrap -i /path/to/exordos-core.raw -m core
```

**Remote repository example (default):**

```bash
exordos bootstrap -i https://repo.exordos.com/exordos-elements/core/0.0.6/ -m core
```

### Key parameters

Run `exordos bootstrap --help` to see all available options. The most important ones are:

| Option | Description |
|---|---|
| `--profile` | Installation profile: `develop`, `small`, `medium`, `large`, or `legacy`. Defaults to `small`. |
| `--cidr IPV4NETWORK` | Main network CIDR for the platform. Defaults to `10.20.0.0/22`. |
| `--core-ip IPV4ADDRESS` | IP address for the core VM. If not set, the second address from `--cidr` is used. |
| `--admin-password TEXT` | Password for the admin user. If not provided, a password is generated automatically. |
| `--save-admin-password-file TEXT` | Save the admin password to a file instead of printing it to the console. |
| `--ssh-public-key PATH` | Path to a public SSH key to inject into the VM. Can be specified multiple times. |
| `--hyper-connection-uri TEXT` | Connection URI for the hypervisor, e.g. `qemu+tcp://10.0.0.1/system` or `qemu+ssh://user@10.0.0.1/system`. |
| `--hyper-storage-pool TEXT` | Libvirt storage pool to use for VM disks. Defaults to `default`. |
| `--force` / `-f` | Force rebuild if the output already exists. |

## Usage

After `exordos bootstrap` completes, the platform is up and running. The command prints the credentials for the admin user to the console (or saves them to a file if `--save-admin-password-file` was specified).

### SSH access

If a public SSH key was provided during bootstrap via `--ssh-public-key`, you can connect directly to the core VM:

```bash
ssh ubuntu@10.20.0.2
```

### API access

Use the admin credentials to obtain an access token from the IAM service:

```bash
curl --location 'http://10.20.0.2:80/api/core/v1/iam/clients/default/actions/get_token/invoke' \
    --header 'Content-Type: application/x-www-form-urlencoded' \
    --data-urlencode 'grant_type=password' \
    --data-urlencode 'username=<ADMIN_USERNAME>' \
    --data-urlencode 'password=<ADMIN_PASSWORD>' \
    --data-urlencode 'scope=' \
    --data-urlencode 'ttl=86400'
```

The `clients/default` path is rewritten by Core's load balancer (port 80) to the real IAM client and has
`X-Client-Id`/`X-Client-Secret` injected automatically, so no client credentials need to be passed here —
see the equivalent setup in `exordos_ecosystem/web/README.md`. The response contains an `access_token`
field. Use this token as a `Bearer` token in all subsequent API requests.

### CLI access

Configure the `exordos` CLI by registering a realm and a context with the admin credentials:

```bash
exordos settings set-realm local --endpoint http://10.20.0.2:80/api/core --current
exordos settings set-context local --name admin -u <ADMIN_USERNAME> -p <ADMIN_PASSWORD> --current
```

The CLI authenticates against the `default` client alias (see above), which only Core's load
balancer on port 80 knows how to rewrite. Pointing the realm at the node's own user-api port
(`:11010`) instead skips the load balancer, so the alias can't be resolved and every command fails
with `Can't parse value: uuid=default.`

- `set-realm` registers the platform endpoint under the name `local` and marks it as the active realm.
- `set-context` creates a named context with the admin credentials and marks it as the active context.

After configuration, you can manage the platform using `exordos` commands, for example:

```bash
exordos compute hypervisors list
exordos elements list
```
