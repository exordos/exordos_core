# RawStor TLS integration

This branch layers runtime mutual TLS on `feat/spiffe-certificates` and requires
the matching `feat/rawstor-tls` SDK branch. Enable the existing Core
`[features] rawstor = true` flag; no separate TLS opt-in is needed. RawStor
services and control operations fail closed while their certificates are pending.
This branch does not start RawStor with `--insecure`.

## Issuance and authorization

The existing Secret Manager issues and renews X.509-SVIDs. A private `rawstor`
certificate profile adds RawStor's signed object permissions to the generic
SPIFFE issuer. The public certificate API rejects this profile. Core creates
its certificate resources under the administrator project, not under the
workload's project, so a project owner cannot mint infrastructure identities.
The CA remains in the issuer-only SPIFFE authority table. Its key is never
included in an agent target or an image.

The installation trust domain is `rawstor.local`. Installations with the same
trust domain still have independent CA keys; peers must trust their configured
CA, not a root presented by a remote peer.

| Consumer | SPIFFE path | Access |
| --- | --- | --- |
| OST | `/ost/STORAGE_NODE_UUID` | Storage member |
| MDS | `/mds/WITNESS_UUID` | Metadata server and witness |
| Core agent control operations | `/admin/AGENT_UUID` | Provider control operations |
| Per-volume vhost | `/account/PROJECT_UUID/client/VOLUME_UUID` | Only its own volume |

The MDS identity UUID is `uuid5(cluster_uuid, "mds-witness")`, matching its
witness topology entry. Exordos projects map to RawStor accounts. A vhost has
role `none` and a single object permission mask `7` (read, write and manage).
Manage permission allows repair to allocate replacement members; it also permits
managing or deleting that object. The certificate grants no access to other
volumes in that account.

Leaves live for seven days. The existing SPIFFE renewal policy renews them during
the last half of that lifetime, preserving identity, permissions and CA while
creating a new key and serial. Builders publish the renewed leaf even when
storage topology, disk size and attachments are unchanged. A certificate is no
longer renewed once neither a desired nor an actual resource references it;
removing a leaf does not revoke already issued certificates or delete its CA.

## Delivery and reload

Certificate/key pairs and the public CA travel in the existing encrypted
universal-agent resource channel. They are generated at runtime, never baked
into an image. The public storage models do not expose these private agent
snapshots.

Agents validate the pair and issuer, then publish an immutable generation under
`/etc/rawstor/tls/` through an atomic `current` symlink. Each generation uses
RawStor’s documented filenames: `cert.crt`, `cert.key` and `ca.crt`. Service private keys are
`root:rawstor` mode `0640`, in directories with mode `0750`. Control-plane keys
are root-only (`0600`, directories `0700`) and cannot be read by RawStor services.
Agent metadata containing the delivered credentials uses the existing owner-only
storage permissions.

The Core agent owns account registration and object creation, growth and deletion.
Its administrative credentials stay on Core. Hypervisors receive only the
per-volume service credentials under `/etc/rawstor/tls/volume-VOLUME_UUID/`.
They observe the object through that identity, refresh vhost after Core grows
it, and remove the local credentials after stopping vhost. Core waits for
attachment removal before deleting the object.

MDS and OST configurations and per-volume vhost systemd drop-ins set
`RAWSTOR_TLS_TRUST_DOMAIN`, `RAWSTOR_TLS_CERT`, `RAWSTOR_TLS_KEY` and
`RAWSTOR_TLS_CA`. The trust domain is read from the URI SAN in `ca.crt`; no
separate `domain` file or trust-domain field is delivered. RawStor 0.3.5 still
requires `RAWSTOR_TLS_TRUST_DOMAIN`, which the agent derives from that CA. Renewals send `SIGUSR1` without restarting the service.
`SIGHUP` remains available for topology reload and disk resize. A failed signal
is retried during reconciliation. Established TLS connections retain their
session; the renewed leaf applies to new connections.

Python bindings have one TLS context per process. Core control operations run
in short-lived child processes with the intended admin credentials, so each
operation loads the current generation and cannot reuse another workload's
context. No plaintext fallback or process-wide single-MDS identity pin is used.

## Deployment

Deploy the matching Core and SDK branches together. Upgrade all certificate
issuer replicas before creating TLS resources. Existing insecure clusters
must be migrated during maintenance: stop workloads and their old vhost units
before switching the services and agents to TLS. An active vhost configured with
`--insecure` is rejected instead of being silently restarted beneath a guest.
For a new managed cluster, Core issues the credentials and its agents configure
the services automatically. Manually managed OSTs also require credentials from
the same installation authority before admission.

This implements leaf renewal. CA rollover is separate: distribute trust in both
roots, replace leaves, drain old sessions, and only then remove the old root.
