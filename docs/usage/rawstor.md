# Rawstor storage

## Enable the experimental integration

Rawstor is disabled by default in Core. To build a development image with it enabled:

```bash
RAWSTOR_ENABLED=true exordos build -f -i ~/.ssh/id_ed25519.pub
```

The build sets the default in the image's Core configuration template. Existing
installations retain their persisted configuration when upgrading. To enable
Rawstor there, set the following in `/etc/exordos_core/exordos_core.conf`:

```ini
[features]
rawstor = true
```

Run `ec-bootstrap --config-file /etc/exordos_core/exordos_core.conf` as root to
synchronize agent capabilities, then restart `ec-user-api` and `ec-gservice`.
Bootstrap keeps the agent configuration copies on the data disk in sync. Persist
the Core configuration change in `/var/lib/exordos/data/etc/exordos_core/exordos_core.conf`
as well so it survives reboot.

With the flag disabled, storage API routes and their OpenAPI descriptions are
hidden, storage builders and schedulers do not start, the MDS agent capability is not registered, and Rawstor pools are excluded from
compute scheduling. Local qcow2 pools remain available. Disabling the flag does
not delete existing storage or stop already running VMs; it suspends management
of that storage. Drain Rawstor workloads before disabling it on an existing stand.

Storage resources are managed through the storage API. Direct storage EM kinds
are not published until that path enforces the same lifecycle and deletion guards.

## Create a cluster

```bash
exordos storages clusters add --type rawstor --name storage1 --host 10.100.0.2
```

The core agent starts a separate `rawstor-mds@UUID.service` with its own
persistent SQLite index and topology. The agent uses the package's template
and writes `/etc/rawstor/mds/UUID.conf` and `UUID.topology`; new databases live
under `/var/lib/rawstor/mds/UUID/`. The first MDS defaults to port 7776.
For subsequent clusters specify `--port` or enter an unused port when prompted.
The cluster endpoint is `mds://<core-ip>:<port>/`.

The public cluster `driver_spec` contains only `kind` and `endpoint`. OSTs and
pool policies are separate resources (`storages nodes` and `storages pools`).
Core assembles their current snapshot only when sending the cluster to its
agent; it is not stored in the public `driver_spec`. `ost_endpoint` belonged to
the old single-OST configuration and is no longer a cluster API field.

Each new cluster has two policies sharing the same OST capacity:

| Pool | Tier | Ephemeral | Mirrors | Failure domain |
| --- | --- | --- | --- | --- |
| persistent | COLD | false | 2 | server |
| ephemeral | COLD | true | 1 | server |

A persistent disk needs at least two distinct servers. A cluster with one
server can schedule ephemeral disks only. Local qcow2 pools and new disk
requests default to WARM and ephemeral; use `--tier cold --no-ephemeral`
for persistent rawstor disks.

A cluster-backed machine volume stores the selected storage pool UUID in
`storage_pool`. The MDS endpoint belongs to the cluster, and mirrors, chunk size
and failure domain belong to the pool. Core resolves `storage_location` and
`storage_policy` when building the volume resource for the hypervisor agent;
these are execution parameters, not separate columns on the volume. Local qcow2
volumes retain their libvirt pool name.

## Initialize and register OSTs

Prepare each storage host and its local agent:

```bash
LOCAL_GENESIS_SDK_PATH=/path/to/gcl_sdk \
  exordos storages nodes init --type rawstor --pool-agent-name my_universal_agent
```

`init` installs librawstor, OST and ZFS support (utilities, DKMS module and
headers for the running kernel), then configures, registers and starts the local
universal agent. It adds `StorageNodeAgentDriver` with a separate state file,
preserving an existing agent's UUID and other drivers for the same core.
`--pool-agent-name` is the local service name and defaults to `universal_agent`.
On this feature branch, `LOCAL_GENESIS_SDK_PATH` selects the matching SDK checkout
for the agent's virtualenv. Omit it once a compatible SDK release is available.
`init` does not create, start or stop an OST. An existing legacy OST keeps
serving until an administrator explicitly migrates it.

Declare each OST through the core API from any host:

```bash
exordos storages nodes add --cluster storage1 --agent AGENT_UUID --name ost1 \
  --location zfs://tank/ost1 --endpoint ost://10.100.0.10:7777 \
  --failure-domain-path dc1/row1/rack1/server1
```

`add --agent` selects a registered agent by its name in core or UUID (printed
by `init`) and creates the OST resource. It does not read local agent configs,
install packages, change agent configuration, start services or wait for OST
readiness. For an OST on another host, specify its advertised `--endpoint`.
The local service name from `--pool-agent-name` can differ from the agent's
registered name; use its UUID to avoid ambiguity.

The agent receives the desired resource through reconciliation, writes
`/etc/rawstor/ost/UUID.conf` and starts the package's `rawstor-ost@UUID.service`
template. A ZFS location must already exist; for a file location the agent
creates the backing directory. Only after the OST answers at its advertised
endpoint does core add it to the MDS topology and reload MDS.

For a native ZFS backing store, specify `--location zfs://POOL/DATASET`.
Provision the zpool separately using explicitly selected storage disks, then
create its dedicated OST dataset, for example:

```bash
sudo zfs create -o mountpoint=none tank/ost1
```

`init` and reconciliation do not create pools, format disks or destroy datasets.
The agent checks that the dataset exists and uses a per-instance systemd drop-in
with `User=root` and `Group=root`, required for native zvol management and block
device access. File-backed instances run as `rawstor` with a `ReadWritePaths`
drop-in for their directory. Deleting an OST retains its backing store.
For independent capacity accounting, use one OST per dedicated zpool; datasets
sharing a pool also share its free space and cannot be counted independently.

`--location` defaults to `file:///var/lib/rawstor/ost/UUID`. Each OST must have a
dedicated backing filesystem; sharing one between OSTs would count its free
space more than once. Multiple OSTs on one host need separate backing filesystems
and bind ports. `--bind` defaults to the host and port of the advertised endpoint.
Rawstor resolves DNS names to IPv4 addresses, including listen addresses.
Use an explicit local `--bind` when the advertised address is not local.
Listening on every interface requires an explicit `--bind 0.0.0.0:PORT`.
When adding on the same host without `--endpoint`, CLI detects the host address
and chooses an unused managed bind port starting at 7777. For a remote host,
provide `--endpoint`. The advertised endpoint may differ from the bind address.
Core rejects duplicate managed bind ports or backing directories on the same host.
Advertised hostnames must use valid DNS labels; use ASCII Punycode for IDNs.

The failure domain path runs from outermost to innermost:
`dc/row/rack/server`. Shorter paths omit outer levels; omitted domains are shared.
`--weight` defaults to 100. Node lists expose the assigned agent and readiness status.

On hypervisors use `exordos hypervisors init` to install the
Python binding and rawstor-vhost.

## Manage resources

Clusters, nodes and pools support `add`, `delete`, `list`, `show` and `update`.
Nodes and pools accept `list --cluster NAME`. Use a UUID where names are
ambiguous across clusters.

```bash
exordos storages clusters list
exordos storages clusters show storage1
exordos storages nodes list --cluster storage1
exordos storages nodes update OST_UUID --weight 200
exordos storages pools list --cluster storage1
exordos storages pools update POOL_UUID --mirrors 2 --failure-domain rack
```

Mirrors, failure domain, tier and ephemeral belong to the pool.
A policy cannot change while disks or pending resources reference the pool.
The chunk size is fixed internally at 1 GiB.
MDS endpoints cannot change after registration. Cluster deletion automatically
removes its empty nodes and pools; deletion is blocked while the cluster has managed
or pending disks, or the OST contains unmanaged objects. Pool removal is
blocked while disks still reference it. Unregistering a cluster retains
its database and data. Node deletion first removes the OST from MDS topology;
after the MDS agent successfully requests reload and reports the desired state,
the host agent stops the OST and removes its config/drop-in. Reload is asynchronous;
Exordos trusts the generated topology and does not parse journal messages.
Backing directories and their data remain. An absent agent report does not prove
that an OST is empty. Deletion requires a reachable, empty OST and MDS topology
acknowledgement; restore an unavailable endpoint before deleting its registration. A deleting cluster stays in `DELETING`
until the agents acknowledge stopping every OST; then its MDS is stopped through
reconciliation. New disks, nodes and pools cannot be added while it is deleting.
The assigned agent and backing location are immutable;
`nodes update --bind IP:PORT --endpoint ost://HOST:PORT` changes connectivity only
when the cluster has no managed disks. Nodes registered before this lifecycle
remain externally managed; their services are not automatically adopted or stopped.

## Shared free space

`clusters show` exposes `capacity_info`: aggregate byte counts, per-OST
budgets, the MDS object inventory and the sample timestamp. Pool availability
is derived from this same inventory; pool figures must never be added together.

Admission accounts for mirrors and distinct failure domains, rounds down to
whole chunks, and reserves pending disks against every possible destination.
API mutations, scheduling and capacity reconciliation share a PostgreSQL
transaction lock. Completed MDS objects are sampled before OST budgets so a
new allocation cannot escape both the physical and pending ledgers. Once an
object appears in the MDS inventory its pending reservation is removed.
Unreachable OSTs contribute zero available space; samples older than 60 seconds
cannot admit new disks. There is no oversubscription.

The estimate is conservative for pending disks and is a placement upper bound
for completed inventory. MDS performs the final placement and can reject an
allocation when its weighted placement cannot fit. Deleting a pending disk
releases its reservation; deleting an allocated disk releases physical space
when OST cleanup is reflected in the next sample. Rawstor disks support grow-only resizing.

## Grow a disk

Increase a VM's root disk in GiB:

```bash
exordos compute nodes update -u VM_UUID --root-disk 20
```

For a disk declared in an element, increase its `disk_spec` size and update the
element. Standalone volumes use `PUT /v1/compute/volumes/VOLUME_UUID` with
`{"size": 20}`. Sizes can only increase; shrinking is rejected before changing
the desired state. The existing pool, mirror count and chunk size are preserved.
Core admits only the additional capacity on the assigned pool, including other
pending allocations. MDS performs the final allocation.

After the object grows, the agent sends `SIGHUP` to its running vhost backend.
The backend reopens its queues and notifies QEMU of the larger disk without
restarting the VM. Failed notifications are retried during discovery; an
inactive backend reads the new size when it next starts. Existing hypervisors
must have the matching updated Rawstor library, Python binding and vhost package.
The guest must expand its partitions and filesystem separately.

## Rawstor version

The default Debian packages and Python binding are version `0.3.4`, from
[Rawstor v0.3.4](https://github.com/rawstor/librawstor/releases/tag/v0.3.4).
Core downloads release packages during image provisioning and checks their
pinned SHA256 checksums. CLI downloads OST, vhost,
librawstor and bindings from the same release. Set `RAWSTOR_VERSION` for a release,
`RAWSTOR_WHEEL_VERSION` when its binding version differs. `nodes init --version VERSION` overrides the
release used for OST and binding installation. Build core with `LOCAL_GENESIS_SDK_PATH`
pointing to the matching `gcl_sdk` checkout on `feat/rawstor`.

## Driver configuration

Storage pools keep `cluster`, `name`, `tier` and `ephemeral` as common fields.
Rawstor settings are nested in `driver_spec`:

```json
{"kind": "rawstor", "mirrors": 2, "failure_domain": "server"}
```

Storage nodes keep their identity, cluster, agent and status as common fields.
Their `driver_spec` contains `kind: rawstor`, `endpoint`, `location`,
`bind_address`, `failure_domain_path` and `weight`.
CLI options remain unchanged; commands construct the nested API configuration.

The pinned build requires a strict majority for writable mirrors. With two
data copies and no witness, both copies must remain available for writes.
Exordos includes the MDS as a metadata-only witness in the topology, advertised
using the MDS bind IP address and port. Its server failure domain remains the
Core node UUID. Rawstor
places a witness for even mirror counts, allowing a two-copy mirror to retain
a majority when one data node is unavailable.

The LIST wire format distinguishes chunks (`1`) from objects (`2`); update
clients, MDS and OST together. Read-only mirrored opens also require quorum;
Rawstor only bypasses it when `FORCE` is explicitly combined with `READONLY`.

### Deployment trust boundary

Rawstor listeners do not authenticate clients. Place MDS, OST and vhost traffic
on a trusted storage network and restrict access with host/network firewall rules.
Storage configuration is an administrator operation: advertised endpoints are
probed by the agent, so only trusted administrators should supply them.
Managed file-backed OST directories must be direct children of
`/var/lib/rawstor/ost`, `/data`, `/srv/rawstor` or `/mnt/rawstor`.
Symlink components and system directories are rejected before ownership changes.

## Run Core from a source checkout

For a source installation on Linux x86-64, install the matching native packages
and enable the pinned `rawstor` Python extra:

```bash
rawstor_packages=$(mktemp -d)
bash exordos/images/download_rawstor.sh "$rawstor_packages" librawstor rawstor-mds
sudo apt-get install "$rawstor_packages/librawstor_0.3.4_amd64.deb" \
  "$rawstor_packages/rawstor-mds_0.3.4_amd64.deb"
uv sync --extra rawstor
```

Use `uv sync --extra rawstor` for subsequent synchronizations as well. The
optional binding is pinned to its GitHub release; native libraries still need
to be installed through the OS package manager. Rawstor images currently require
amd64; Core images with the integration disabled also support arm64.

Every downloaded asset must have a committed digest in `SHA256SUMS`. Version
overrides without a trusted digest are rejected before installation.
The core image's installation script performs the binding installation itself.

## Project accounts and upgrade compatibility

Rawstor account UUIDs match Exordos `project_id`. Before creating the first disk
of a project in an MDS, the hypervisor registers that account idempotently.
Object and vhost locations carry it as URI user information, for example
`mds://PROJECT_UUID@core:7776/OBJECT_UUID`. Discovery restores the project from
the saved location. The nil project UUID cannot be used: Rawstor reserves it
for unfiltered listings, not ownership.

Cluster and OST capacity queries remain unfiltered, covering every project in
the shared physical budget. A account is not automatically removed when a disk
is deleted: Rawstor account removal also deletes that account's objects and
versions. This integration runs Rawstor in explicit insecure mode; account UUIDs are
ownership metadata, and access control remains in Exordos.

Build 44297955553 requires a protocol handshake and account-aware wire frames
and metadata. Upgrade librawstor, Python bindings, MDS, OST and vhost together.
An MDS database or chunk metadata written before accounts is incompatible;
Rawstor provides no migration. Recreate a disposable development cluster for
this build. Updating packages does not convert or delete existing storage.

## Transport mode

The integration currently starts MDS, OST and vhost with explicit
`EXTRA_ARGS="--insecure"`. Connections are plaintext and peers are not
authenticated. TLS and SPIFFE issuance are maintained in a separate branch
layered on the storage integration.

Agents and the API explicitly call `rawstor.initialize(insecure=True)` before
loading native `Target` or `Location` types. This matches the service transport
mode; setting any `RAWSTOR_TLS_*` options in insecure mode is rejected.
