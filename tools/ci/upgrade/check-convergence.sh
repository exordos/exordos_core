#!/usr/bin/env bash

# Copyright 2026 Genesis Corporation
#
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
#
# Assert that the control plane and the data plane agree.
#
# This is the check that a status listing cannot give you.  Resources keep
# reporting ACTIVE while the agent re-applies them in a loop forever, which is
# exactly how a lost payload field hides: the target hash and the actual hash
# never meet, so every reconcile iteration decides there is work to do.
# `hash` — not `full_hash` — is what the agent compares when it makes that
# decision, so that is what is asserted here; `full_hash` is only reported, as
# the two sides may legitimately differ on fields outside the target set.
#
# Only target resources the scheduler has given to an agent are asserted on.
# `/v1/ua/target_resources/` is not a list of things a data plane renders: on a
# freshly bootstrapped installation 966 of its 1069 rows carry no agent at all,
# and they are supposed to. Most are the repository catalogue
# (`repo_proxy_element`, one row per element version a repository offers); the
# rest are instance-level rows (`node`, `machine`, `vs_variable`) whose
# derivatives are the things that go to agents. Demanding that all of them
# converge asserts that the core is doing something it never claimed to, and
# fails every run before an upgrade is even attempted.
#
# What the unscheduled rows *can* hide — a capability no agent claims, so the
# work is never handed out — is caught by comparing the snapshots instead: a
# kind that was scheduled before the upgrade and is not after is a regression,
# and that is a question about two runs, not about one listing.
#
# Usage: check-convergence.sh [report.json]
#
# Convergence must hold on several consecutive polls: a single clean poll can
# be caught mid-way through a legitimate rebuild.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tools/ci/upgrade/lib.sh
source "${SCRIPT_DIR}/lib.sh"

TIMEOUT="${UPGRADE_CONVERGENCE_TIMEOUT:-900}"
STABLE_POLLS="${UPGRADE_CONVERGENCE_STABLE_POLLS:-2}"
STABLE_INTERVAL="${UPGRADE_CONVERGENCE_STABLE_INTERVAL:-60}"
RETRY_INTERVAL="${UPGRADE_CONVERGENCE_RETRY_INTERVAL:-15}"

report_file="${1:-}"
work_dir="$(mktemp -d)"
trap 'rm -rf "${work_dir}"' EXIT

# Emits one object per target resource that has not converged.
divergences() {
    core_get /v1/ua/target_resources/ >"${work_dir}/targets.json" || return 1
    core_get /v1/ua/resources/ >"${work_dir}/actual.json" || return 1

    jq -n \
        --slurpfile targets "${work_dir}/targets.json" \
        --slurpfile actual "${work_dir}/actual.json" \
        '
        ($actual[0] | map({key: "\(.kind)/\(.res_uuid)", value: .}) | from_entries) as $by_key
        | $targets[0]
        | map(select(.agent != null))
        | map(
            . as $target
            | "\($target.kind)/\($target.res_uuid)" as $key
            | $by_key[$key] as $actual_resource
            | if $actual_resource == null then
                  {key: $key, reason: "the data plane never reported this resource",
                   target_status: $target.status}
              elif $actual_resource.hash != $target.hash then
                  {key: $key, reason: "hash mismatch: the data plane cannot reproduce the target",
                   target_status: $target.status,
                   target_hash: $target.hash, actual_hash: $actual_resource.hash,
                   target_full_hash: $target.full_hash,
                   actual_full_hash: $actual_resource.full_hash}
              elif $target.status != "ACTIVE" then
                  {key: $key, reason: "target resource is stuck outside ACTIVE",
                   target_status: $target.status}
              else empty end)
        '
}

deadline=$((SECONDS + TIMEOUT))
clean_polls=0
last_report="[]"

while ((SECONDS < deadline)); do
    if ! last_report="$(divergences)"; then
        log "cannot read the resource lists, retrying"
        clean_polls=0
        sleep "${RETRY_INTERVAL}"
        continue
    fi
    count="$(jq -r 'length' <<<"${last_report}")"

    if ((count == 0)); then
        clean_polls=$((clean_polls + 1))
        log "converged (${clean_polls}/${STABLE_POLLS} consecutive polls clean)"
        if ((clean_polls >= STABLE_POLLS)); then
            total="$(jq -r 'map(select(.agent != null)) | length' "${work_dir}/targets.json")"
            ((total > 0)) || die "nothing is scheduled to any agent — the installation is not doing anything"
            log "all ${total} scheduled target resources match the data plane"
            [[ -z "${report_file}" ]] || printf '[]\n' >"${report_file}"
            exit 0
        fi
        sleep "${STABLE_INTERVAL}"
        continue
    fi

    if ((clean_polls > 0)); then
        log "convergence broke after ${clean_polls} clean poll(s), restarting the count"
    fi
    clean_polls=0
    log "${count} resource(s) not converged yet"
    sleep "${RETRY_INTERVAL}"
done

jq -r '.[] | "  \(.key): \(.reason) [target=\(.target_status)]"' <<<"${last_report}" >&2
[[ -z "${report_file}" ]] || jq -S '.' <<<"${last_report}" >"${report_file}"
die "control plane and data plane did not converge within ${TIMEOUT}s"
