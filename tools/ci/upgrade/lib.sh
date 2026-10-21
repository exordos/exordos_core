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
# Shared helpers for the core upgrade end-to-end CI job.
#
# The job bootstraps the previous release, puts a workload on it, upgrades the
# core to the freshly built version and then asserts that the installation both
# survived and converged.
#
# Actions go through the `exordos` CLI, because that is what a real operator
# uses.  Assertions go through the raw user API, so that they depend on the API
# contract rather than on how the CLI happens to render a table today.

set -euo pipefail

CORE_ENDPOINT_URL="${CORE_ENDPOINT_URL:-http://10.20.0.2:80/api/core}"
CORE_USERNAME="${CORE_USERNAME:-admin}"
CORE_PASSWORD="${CORE_PASSWORD:-admin}"
CORE_CLIENT="${CORE_CLIENT:-default}"

# The workload uses deterministic names so that the "seed" and "verify" phases
# need no state passed between workflow steps.
MARKER_ORGANIZATION="upgrade-e2e-organization"
MARKER_PROJECT="upgrade-e2e-project"
MARKER_PROJECT_AFTER="upgrade-e2e-project-after"
# `project_id` is required and has no default: restalchemy declares it
# read-only with no fallback, so project-scoped resources take it in the
# payload — the same way `exordos cn add` demands `--project-id`. The service
# project is the one the installation already runs its own nodes in, so it is
# known to have quota; a freshly created tenant project would not be.
MARKER_PROJECT_ID="00000000-0000-0000-0000-000000000000"
MARKER_PG_INSTANCE="upgrade-e2e-pg"
MARKER_PG_USER="upgrade_e2e_user"
MARKER_PG_PASSWORD="upgrade-e2e-password"
MARKER_PG_DATABASE="upgrade_e2e_db"
MARKER_TABLE="upgrade_marker"

DBAAS_ELEMENT="${DBAAS_ELEMENT:-dbaas}"
DBAAS_API_PORT="${DBAAS_API_PORT:-8080}"

log() { printf '[%(%H:%M:%S)T] %s\n' -1 "$*" >&2; }

die() {
    log "FAILED: $*"
    exit 1
}

# --- API access --------------------------------------------------------------

_core_token=""
_core_token_issued_at=0

# Tokens are short lived and the job outlives them, so re-issue periodically.
#
# This must not abort the process on failure: the API is legitimately gone
# while the core replaces its image and reboots, and the wait loops need a
# plain non-zero result to keep polling.
core_token() {
    local now
    now="$(date +%s)"
    if [[ -z "${_core_token}" ]] || ((now - _core_token_issued_at > 240)); then
        _core_token="$(
            curl -fsS -X POST \
                "${CORE_ENDPOINT_URL}/v1/iam/clients/${CORE_CLIENT}/actions/get_token/invoke" \
                -H 'Content-Type: application/x-www-form-urlencoded' \
                --data-urlencode "username=${CORE_USERNAME}" \
                --data-urlencode "password=${CORE_PASSWORD}" \
                --data-urlencode 'grant_type=password' |
                jq -er '.access_token'
        )" || return 1
        _core_token_issued_at="${now}"
    fi
    printf '%s' "${_core_token}"
}

# api_request <method> <base_url> <path> [json_body]
#
# `curl -f` throws the response body away, which turns every rejected payload
# into a bare "error 400" and leaves nothing to debug. Capture the body and
# print it instead. Callers that poll silence stderr, so this stays quiet
# exactly where failures are expected.
api_request() {
    local method="$1" base="$2" path="$3" body="${4:-}"
    local response status
    response="$(mktemp)"

    local -a args=(
        -sS -o "${response}" -w '%{http_code}'
        -X "${method}"
        -H "Authorization: Bearer $(core_token)"
    )
    if [[ -n "${body}" ]]; then
        args+=(-H 'Content-Type: application/json' --data "${body}")
    fi

    if ! status="$(curl "${args[@]}" "${base}${path}")"; then
        log "${method} ${path}: no response from ${base}"
        rm -f "${response}"
        return 1
    fi

    if ((status >= 400)); then
        log "${method} ${path}: HTTP ${status}"
        log "  request:  ${body:-<empty>}"
        log "  response: $(tr -d '\n' <"${response}")"
        rm -f "${response}"
        return 1
    fi

    cat "${response}"
    rm -f "${response}"
}

api_get() { api_request GET "$1" "$2"; }

api_post() { api_request POST "$1" "$2" "$3"; }

core_get() { api_get "${CORE_ENDPOINT_URL}" "$1"; }

core_post() { api_post "${CORE_ENDPOINT_URL}" "$1" "$2"; }

# --- waiting -----------------------------------------------------------------

# wait_for <timeout_s> <interval_s> <description> <command...>
#
# The command is expected to be quiet; redirect inside the predicate if needed.
wait_for() {
    local timeout="$1" interval="$2" description="$3"
    shift 3

    local deadline=$((SECONDS + timeout))
    while ((SECONDS < deadline)); do
        if "$@"; then
            log "ok: ${description}"
            return 0
        fi
        sleep "${interval}"
    done
    die "timed out after ${timeout}s waiting for: ${description}"
}

core_api_is_up() {
    core_get "/v1/em/elements/" >/dev/null 2>&1
}

# element_is_active <name> [version]
#
# Every row for the element must be ACTIVE, and at least one row must exist —
# an element with no rows is a missing element, not a converged one.
element_is_active() {
    local name="$1" version="${2:-}" verdict
    verdict="$(
        core_get "/v1/em/elements/?name=${name}" 2>/dev/null |
            jq -r --arg version "${version}" '
                map(select($version == "" or .version == $version))
                | if length == 0 then "MISSING"
                  elif all(.status == "ACTIVE") then "ACTIVE"
                  else "PENDING" end
            '
    )" || return 1
    [[ "${verdict}" == "ACTIVE" ]]
}

# --- repositories ------------------------------------------------------------

# Repository listings are only as fresh as the last refresh, and both what to
# install and what to upgrade to are read from them.
refresh_repositories() {
    local uuid
    for uuid in $(core_get /v1/repo/repositories/ | jq -r '.[].uuid'); do
        core_post "/v1/repo/repositories/${uuid}/actions/refresh/invoke" '{}' >/dev/null ||
            log "warning: cannot refresh repository ${uuid}"
    done
}

# --- element lookups ---------------------------------------------------------

element_uuid() { # <name>
    core_get "/v1/em/elements/?name=${1}" | jq -er '.[0].uuid' ||
        die "element ${1} is not installed"
}

# Nodes an element owns, as "<name> <ipv4>" lines.
element_node_addresses() { # <name>
    local uuid node_uuid
    uuid="$(element_uuid "$1")"
    for node_uuid in $(
        core_get "/v1/em/elements/${uuid}/resources/?kind=em_core_compute_nodes" |
            jq -r '.[].uuid'
    ); do
        core_get "/v1/compute/nodes/${node_uuid}" |
            jq -r '"\(.name) \(.default_network.ipv4 // "")"'
    done
}

# The element's API endpoint, found by probing its nodes rather than by
# guessing which node holds the control plane.
dbaas_api_url() {
    local name address url
    while read -r name address; do
        [[ -n "${address}" ]] || continue
        url="http://${address}:${DBAAS_API_PORT}"
        if api_get "${url}" "/v1/types/postgres/versions/" >/dev/null 2>&1; then
            printf '%s' "${url}"
            return 0
        fi
        log "node ${name} (${address}) does not serve the dbaas API"
    done < <(element_node_addresses "${DBAAS_ELEMENT}")
    die "no node of element ${DBAAS_ELEMENT} serves the API on port ${DBAAS_API_PORT}"
}
