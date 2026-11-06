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
# Compare the installation before and after the upgrade.
#
# Usage: compare-snapshots.sh <before-dir> <after-dir>
#
# Two failure modes are checked here that a per-object status cannot show:
# things silently disappearing, and things silently doubling.  Re-running the
# bootstrap against an already bootstrapped database is how duplicates get in,
# and an upgrade runs the bootstrap again by design.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tools/ci/upgrade/lib.sh
source "${SCRIPT_DIR}/lib.sh"

before_dir="${1:?usage: compare-snapshots.sh <before-dir> <after-dir>}"
after_dir="${2:?usage: compare-snapshots.sh <before-dir> <after-dir>}"

failures=0

fail() {
    log "FAILED: $*"
    failures=$((failures + 1))
}

# duplicates <file> <jq key expression> <what>
duplicates() {
    local duplicated
    duplicated="$(jq -r "
        group_by($2) | map(select(length > 1)) | .[] | \"\(.[0] | $2) x\(length)\"
    " "${after_dir}/$1")"
    if [[ -n "${duplicated}" ]]; then
        fail "duplicated $3 after the upgrade:"$'\n'"${duplicated}"
    fi
}

duplicates target_resources.json '"\(.kind)/\(.res_uuid)"' "target resources"
duplicates resources.json '"\(.kind)/\(.res_uuid)"' "actual resources"
duplicates elements.json '"\(.name)/\(.version)"' "elements"

# Everything installed before must still be installed.  The core itself is
# excluded: replacing its row with a new version is the point of the upgrade.
missing_elements="$(
    jq -r -n \
        --slurpfile before "${before_dir}/elements.json" \
        --slurpfile after "${after_dir}/elements.json" \
        '($after[0] | map(.name) | unique) as $present
         | $before[0] | map(select(.name != "core") | .name) | unique
         | map(select(. as $name | $present | index($name) | not))
         | .[]'
)"
if [[ -n "${missing_elements}" ]]; then
    fail "elements lost during the upgrade: ${missing_elements//$'\n'/, }"
fi

# A capability that stops being claimed is invisible to the convergence check —
# unscheduled work is not divergent work, it is work that never starts. It shows
# up here instead: the upgrade may add kinds, but a kind the scheduler handed to
# an agent before and hands to nobody after is the data plane silently losing a
# capability.
unscheduled_kinds="$(
    jq -r -n \
        --slurpfile before "${before_dir}/target_resources.json" \
        --slurpfile after "${after_dir}/target_resources.json" \
        '($after[0] | map(select(.agent != null) | .kind) | unique) as $now
         | $before[0] | map(select(.agent != null) | .kind) | unique
         | map(select(. as $kind | $now | index($kind) | not))
         | .[]'
)"
if [[ -n "${unscheduled_kinds}" ]]; then
    fail "kinds no longer scheduled to any agent: ${unscheduled_kinds//$'\n'/, }"
fi

before_nodes="$(jq -r '.nodes' "${before_dir}/counts.json")"
after_nodes="$(jq -r '.nodes' "${after_dir}/counts.json")"
if ((after_nodes < before_nodes)); then
    fail "compute nodes disappeared: ${before_nodes} -> ${after_nodes}"
fi

log "counts before: $(tr -d '\n ' <"${before_dir}/counts.json")"
log "counts after:  $(tr -d '\n ' <"${after_dir}/counts.json")"

((failures == 0)) || die "${failures} snapshot comparison(s) failed"
log "no objects were lost or duplicated by the upgrade"
