# Upgrade testing

An installation that is already running is the hard case. A fresh bootstrap
starts from an empty database, an empty data disk and no agents in the field;
an upgrade starts from data written by an older version of the same code, and
has to keep serving it. The `UpgradeE2E` job in `.github/workflows/build.yml`
is what covers that case.

## What the job does

It runs next to `FuncTests` — on every push, and once more when a pull request
is opened — and reuses the same platform: a GitHub-hosted runner that turns
itself into a hypervisor and boots the core as a real virtual machine.

A pull request from a fork is deliberately not built: `Build` runs on our own
machine with the push credentials, so it refuses untrusted code, and every job
downstream depends on the artifacts it produces. Branches in this repository
are covered either way, because `push` fires for them.

1. **Resolve the version pair.** `NEW` is the version this run built, taken
   from the build artifacts. `PREV` is the newest release tag that is not
   `NEW` — what an existing installation is expected to be running. The base
   release image must be published, and the job fails loudly if it is not:
   skipping would turn the whole test into a green light that checks nothing.
2. **Bootstrap `PREV`** with the public CLI, exactly as an operator would.
3. **Put a workload on it**: core-level objects (an organization and a
   project), the third-party `dbaas` element with its own nodes, a postgres
   instance it provisions, and a marker row inside that database.
4. **Snapshot and check convergence** *before* the upgrade — a baseline,
   without which a divergence found afterwards cannot be blamed on the upgrade.
5. **Upgrade the core** with `exordos ee update core -v NEW`, waiting for the
   API to disappear and come back.
6. **Assert.** Convergence again, snapshot comparison, and the workload check.

## The assertion that matters

Statuses lie. A resource keeps reporting `ACTIVE` while the agent re-applies it
in a loop forever, and that is exactly how a lost payload field hides: the
control plane sends a field the data plane drops on the way back, the two
hashes never meet, and every reconcile iteration decides there is still work to
do. An installation in that state looks healthy in `ee list` and serves
requests, while its journal fills with errors.

So the job compares hashes rather than statuses. For every target resource
there must be an actual resource with the same `(kind, res_uuid)`, and their
`hash` values must be equal — `hash`, not `full_hash`, because `hash` covers
the target fields and is what the agent compares when it decides whether to act.
The two sides may legitimately differ on `full_hash`, so that is reported for
diagnosis but never fails the job.

Convergence has to hold on several consecutive polls a minute apart. A single
clean poll can be caught in the middle of a legitimate rebuild.

## What else is checked

- Nothing was lost: every element installed before is still installed, and the
  node count did not drop.
- Nothing was duplicated: no repeated `(kind, res_uuid)` resources and no
  repeated `(name, version)` elements. An upgrade re-runs the bootstrap against
  an already bootstrapped database, which is how duplicates get in.
- The data survived *and* the paths are still alive: the pre-upgrade marker row
  is still there, a new row can be written, and a new project can be created.
  Surviving data on its own does not prove the control plane still works.
- Only one core version is `ACTIVE` afterwards.

## Layout

The workflow steps are thin; the logic is in `tools/ci/upgrade/`:

| Script | Purpose |
| --- | --- |
| `lib.sh` | API access, waiting, element lookups, marker names |
| `resolve-versions.sh` | Pick `PREV`/`NEW`, verify the base image is published |
| `workload.sh` | `seed` the load, `verify` it survived |
| `snapshot.sh` | Dump the state that must not be damaged |
| `check-convergence.sh` | Compare target and actual hashes until stable |
| `upgrade-core.sh` | Run the upgrade and wait for the installation to return |
| `compare-snapshots.sh` | Detect lost and duplicated objects |

Actions go through the `exordos` CLI, because that is what an operator uses.
Assertions go through the raw user API, so they depend on the API contract
rather than on how the CLI renders a table today.

Snapshots and any divergence report are uploaded as job artifacts, so a
failure can be diagnosed without re-running the job.

## Known limitation

The core replaces its own image and reboots, and there is no way back if the
new image cannot be fetched: the update data is moved off the data disk before
the download is attempted, so a failed download leaves a machine that boots
into an updater with nothing to apply. The job checks its preconditions up
front and has a timeout; the installation it uses is disposable, so a brick
costs a red job and nothing more.

Database schema is not covered here — it cannot be reached from outside the
core node. Comparing an upgraded schema against a freshly installed one belongs
in a cheaper, database-only job.
