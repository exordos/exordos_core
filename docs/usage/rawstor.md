# Rawstor storage

## Create a cluster

```bash
exordos storages clusters add --type rawstor --name storage1 --host 10.100.0.2
```

The core agent starts a separate `rawstor-mds@UUID.service` with its own
persistent SQLite index and topology. The agent uses the package's template
and writes `/etc/rawstor/mds/UUID.conf` and `UUID.topology`; new databases live
under `/var/lib/rawstor-mds/UUID/`. Existing Exordos MDS databases stay at their
original paths through a config override. The first MDS defaults to port 7776.
For subsequent clusters specify `--port` or enter an unused port when prompted.
The cluster endpoint is `mds://<core-ip>:<port>/`.

The public cluster `driver_spec` contains only `kind` and `endpoint`. OSTs and
pool policies are separate resources (`storages nodes` and `storages pools`).
Core assembles their current snapshot only when sending the cluster to its
agent; it is not stored in the public `driver_spec`. `ost_endpoint` belonged to
the old single-OST configuration and is no longer a cluster API field.

Each new cluster has two policies sharing the same OST capacity:

| Pool | Speed | Ephemeral | Mirrors | Failure domain | Chunk size |
| --- | --- | --- | --- | --- | --- |
| persistent | WARM | false | 2 | server | 1 GiB |
| ephemeral | WARM | true | 1 | server | 1 GiB |

A persistent disk needs at least two distinct servers. A cluster with one
server can schedule ephemeral disks only. Local qcow2 pools and new disk
requests default to HOT and ephemeral; use `--speed warm --no-ephemeral`
for persistent rawstor disks.

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
`init` does not create or start an OST.

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

`--location` defaults to `file:///var/lib/rawstor/UUID`. Each OST must have a
dedicated backing filesystem; sharing one between OSTs would count its free
space more than once. Multiple OSTs on one host need separate backing filesystems
and bind ports. `--bind` defaults to `0.0.0.0` and the advertised endpoint port.
When adding on the same host without `--endpoint`, CLI detects the host address
and chooses an unused managed bind port starting at 7777. For a remote host,
provide `--endpoint`. The advertised endpoint may differ from the bind address.
Core rejects duplicate managed bind ports or backing directories on the same host.

The failure domain path runs from outermost to innermost:
`dc/row/rack/server`. Shorter paths omit outer levels; omitted domains are shared.
`--weight` defaults to 1. Node lists expose the assigned agent and readiness status.

On hypervisors use `exordos hypervisors init --with-rawstor` to install the
Python binding and rawstor-vhost.

## Manage resources

Clusters, nodes and pools support `add`, `delete`, `list`, `show` and `update`.
Nodes and pools accept `list --cluster NAME`. Use a UUID where names are
ambiguous across clusters.

```bash
exordos storages clusters list
exordos storages clusters show storage1
exordos storages nodes list --cluster storage1
exordos storages nodes update OST_UUID --weight 2
exordos storages pools list --cluster storage1
exordos storages pools update POOL_UUID --mirrors 2 --failure-domain rack \
  --chunk-size 64MiB
```

Mirrors, failure domain, chunk size, speed and ephemeral belong to the pool.
Changing a policy applies to newly created disks; existing disks retain the
policy recorded when scheduled. Chunk sizes must be powers of two, up to 1 GiB.
MDS endpoints cannot change after registration. Remove empty nodes before
removing a cluster; node removal is blocked while the cluster has managed
or pending disks, or the OST contains unmanaged objects. Pool removal is
blocked while disks still reference it. Unregistering a cluster retains
its database and data. Node deletion first removes the OST from MDS topology;
after the MDS agent successfully requests reload and reports the desired state,
the host agent stops the OST and removes its config/drop-in. Reload is asynchronous;
Exordos trusts the generated topology and does not parse journal messages.
Backing directories and their data remain. Wait for node deletion to reconcile
before deleting the cluster. The assigned agent and backing location are immutable;
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
when OST cleanup is reflected in the next sample. Rawstor disk resizing is
currently unsupported.

## Rawstor version

The default Debian version is `99.0.0`, with Python binding
`99.0.0+0.fe3340e`, from
[Actions run 37329790134](https://github.com/rawstor/librawstor/actions/runs/37329790134).
Core bundles its packages and SHA256 checksums. CLI downloads OST, vhost,
librawstor and bindings from the same run. Set `RAWSTOR_VERSION` for a release,
`RAWSTOR_ARTIFACT_RUN` for another CI run, and `RAWSTOR_WHEEL_VERSION` when its
binding version differs. `nodes init --version VERSION` overrides the
release used for OST and binding installation. Build core with `LOCAL_GENESIS_SDK_PATH`
pointing to the matching `gcl_sdk` checkout on `feat/rawstor`.
