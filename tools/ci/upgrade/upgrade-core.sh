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
# Upgrade the core of a running installation and wait for it to come back.
#
# Usage: upgrade-core.sh <new-version>
#
# The core replaces its own image and reboots, and there is no way back if the
# new image cannot be fetched: the update data is moved off the data disk
# before the download is attempted, so a failed download leaves a machine that
# boots into an updater with nothing to apply.  The installation here is
# disposable, but the preconditions are still checked up front so that a
# failure says what went wrong instead of hanging until the job times out.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tools/ci/upgrade/lib.sh
source "${SCRIPT_DIR}/lib.sh"

API_RETURN_TIMEOUT="${UPGRADE_API_RETURN_TIMEOUT:-900}"
CORE_ACTIVE_TIMEOUT="${UPGRADE_CORE_ACTIVE_TIMEOUT:-900}"
REIMAGE_TIMEOUT="${UPGRADE_REIMAGE_TIMEOUT:-900}"

# The set every core node belongs to (`exordos_core.common.constants`).
CORE_SET_UUID="70c88222-b4d9-46c3-9340-aa5bfaaa4b94"

core_node_uuid() {
    core_get /v1/compute/nodes/ |
        jq -er --arg set "${CORE_SET_UUID}" \
            'map(select((.node_set // "") | contains($set))) | .[0].uuid'
}

# What the data plane last reported for the core node's machine. `hash` covers
# the target fields, and the image is one of them, so this changes exactly when
# the machine is running a different image than it was.
core_machine_hash() { # <node uuid>
    core_get /v1/ua/resources/ 2>/dev/null |
        jq -er --arg node "$1" \
            'map(select(.kind == "guest_machine" and .node == $node)) | .[0].hash'
}

new_version="${1:?usage: upgrade-core.sh <new-version>}"

# The version to upgrade to is read from a repository listing, which is only as
# fresh as the last refresh.
refresh_repositories

# `ee update -v` only considers repository rows in AVAILABLE state, so a
# version that is not offered — or is already installed — silently has nothing
# to update to.
offered_status="$(
    core_get "/v1/repo/elements/?name=core" |
        jq -r --arg version "${new_version}" '
            map(select(.version == $version)) | .[0].status // "MISSING"
        '
)"
case "${offered_status}" in
AVAILABLE) ;;
MISSING)
    core_get "/v1/repo/elements/?name=core" | jq -r '.[] | "  \(.version) \(.status)"' >&2
    die "core ${new_version} is not offered by any repository (see the versions above)"
    ;;
*)
    die "core ${new_version} is in state ${offered_status}, not AVAILABLE — nothing to update to"
    ;;
esac

# Recorded before the update, because the point of it is to change.
core_node="$(core_node_uuid)" || die "cannot find this installation's core node"
image_before="$(core_machine_hash "${core_node}")" ||
    die "the core node's machine is not reported by any agent"

log "upgrading core to ${new_version}"
started_at="${SECONDS}"
exordos ee update core -v "${new_version}" -y

# The API disappears while the image is replaced and the machine reboots.
wait_for "${API_RETURN_TIMEOUT}" 5 "the core API to come back" core_api_is_up

# ...but "the API answers" is not "the upgrade happened". The element row flips
# to the new version, and the API keeps serving, for as long as it takes the
# machine to be told to re-image itself: measured on a stand, the row was ACTIVE
# and the API up 4s after `ee update` returned, while the real outage started a
# minute later and lasted about two and a half. A job that believed the first
# signal declared success, went on to snapshot an installation still running the
# old image, and then died on a 502 when the reboot finally came.
#
# The machine having been re-imaged is a data-plane fact, so ask the data plane:
# the core node's `guest_machine` resource is re-reported with a different hash
# once the agent has applied the new image, and cannot say so before.
image_changed() {
    local now
    now="$(core_machine_hash "${core_node}")" || return 1
    [[ "${now}" != "${image_before}" ]]
}
wait_for "${REIMAGE_TIMEOUT}" 10 \
    "the core node's machine to report a new image" image_changed
log "core was replaced and came back after about $((SECONDS - started_at))s"

wait_for "${CORE_ACTIVE_TIMEOUT}" 10 "core ${new_version} to become ACTIVE" \
    element_is_active core "${new_version}"

# A stale row left behind is worth reporting; a second *active* core is a
# broken upgrade, not leftovers.
core_get "/v1/em/elements/?name=core" | jq -r '.[] | "  core \(.version) \(.status)"' >&2
other_active="$(
    core_get "/v1/em/elements/?name=core" |
        jq -r --arg version "${new_version}" '
            map(select(.version != $version and .status == "ACTIVE") | .version)
            | unique | join(", ")
        '
)"
[[ -z "${other_active}" ]] ||
    die "core ${new_version} is ACTIVE, but so are older versions: ${other_active}"
