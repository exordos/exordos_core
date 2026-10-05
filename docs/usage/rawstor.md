# Rawstor storage

## Create a cluster

```bash
exordos storages clusters add --type rawstor --name storage1 --mds-host 10.100.0.2
```

The core agent starts a separate `rawstor-mds@UUID.service` with its own
persistent SQLite index and topology. The first MDS defaults to port 7776.
For subsequent clusters specify `--mds-port` or enter an unused port when prompted.
The cluster endpoint is `mds://<core-ip>:<port>/`.

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

Run initialization on each storage host:

```bash
exordos storages nodes init --type rawstor --name ost1 \
  --location file:///data/rawstor --bind 0.0.0.0:7777 \
  --endpoint ost://10.100.0.10:7777
exordos storages nodes add --cluster storage1 --name ost1 \
  --failure-domain-path dc1/row1/rack1/server1
```

`init` installs librawstor and OST, starts `rawstor-ost@UUID.service`, and
stores its identity in `/etc/rawstor-ost/instances/NAME.json`. Re-running
initialization retains the UUID. Each OST must have a dedicated backing
filesystem; sharing a filesystem between OSTs would count its free space
more than once. Multiple OSTs on one host need separate backing filesystems
and bind ports. The advertised endpoint may differ from the bind address.

`add` reads the local initialized instance. For remote registration supply
both `--uuid` and `--endpoint`; the UUID must remain stable. The failure domain
path runs from outermost to innermost: `dc/row/rack/server`. Shorter paths omit
outer levels; omitted domains are shared. `--weight` defaults to 1.
Registration updates the MDS topology and reloads the service.

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
its database and data. OST services are managed separately on their hosts.

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
`99.0.0+0.4e3d1f3`, from
[Actions run 37239522275](https://github.com/rawstor/librawstor/actions/runs/37239522275).
Core bundles its packages and SHA256 checksums. CLI downloads OST, vhost,
librawstor and bindings from the same run. Set `RAWSTOR_VERSION` for a release,
`RAWSTOR_ARTIFACT_RUN` for another CI run, and `RAWSTOR_WHEEL_VERSION` when its
binding version differs. `nodes init --rawstor-version VERSION` overrides the
release used for OST installation. Build core with `LOCAL_GENESIS_SDK_PATH`
pointing to the matching `gcl_sdk` checkout on `feat/rawstor`.
