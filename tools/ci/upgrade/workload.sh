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
# The workload the upgrade has to survive.
#
#   seed   - put load on the freshly bootstrapped previous release: core-level
#            data (an organization and a project), a third-party element
#            (dbaas) with its own nodes, and a real row in a database the
#            element provisioned.
#   verify - after the upgrade, assert that all of it survived AND that the
#            paths are still alive: new objects can be created, new rows can be
#            written.  Surviving data alone would not prove the control plane
#            still works.
#
# Usage: workload.sh seed|verify

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tools/ci/upgrade/lib.sh
source "${SCRIPT_DIR}/lib.sh"

ELEMENT_TIMEOUT="${UPGRADE_ELEMENT_TIMEOUT:-2400}"
INSTANCE_TIMEOUT="${UPGRADE_INSTANCE_TIMEOUT:-2400}"

# --- core-level data ---------------------------------------------------------

organization_uuid() {
    core_get "/v1/iam/organizations/?name=${MARKER_ORGANIZATION}" | jq -er '.[0].uuid'
}

project_uuid() { # <name>
    core_get "/v1/iam/projects/?name=${1}" | jq -er '.[0].uuid'
}

create_project() { # <name> <organization uuid>
    core_post /v1/iam/projects/ "$(
        jq -n --arg name "$1" --arg organization "/v1/iam/organizations/$2" \
            '{name: $name, organization: $organization}'
    )" >/dev/null
}

seed_core_data() {
    log "creating core-level marker objects"
    core_post /v1/iam/organizations/ \
        "$(jq -n --arg name "${MARKER_ORGANIZATION}" '{name: $name}')" >/dev/null
    create_project "${MARKER_PROJECT}" "$(organization_uuid)"
}

verify_core_data() {
    local organization project
    organization="$(organization_uuid)" ||
        die "organization ${MARKER_ORGANIZATION} did not survive the upgrade"
    project="$(project_uuid "${MARKER_PROJECT}")" ||
        die "project ${MARKER_PROJECT} did not survive the upgrade"
    log "core-level markers survived (organization=${organization} project=${project})"

    # Data surviving is not enough: the write path has to work afterwards too.
    create_project "${MARKER_PROJECT_AFTER}" "${organization}" ||
        die "cannot create a project after the upgrade"
    project_uuid "${MARKER_PROJECT_AFTER}" >/dev/null ||
        die "project created after the upgrade is not readable back"
    log "core write path works after the upgrade"
}

# --- third-party element -----------------------------------------------------

seed_element() {
    log "installing element ${DBAAS_ELEMENT}"
    refresh_repositories
    core_get "/v1/repo/elements/?name=${DBAAS_ELEMENT}" | jq -e 'length > 0' >/dev/null ||
        die "element ${DBAAS_ELEMENT} is not offered by any repository"

    exordos ee install "${DBAAS_ELEMENT}"
    wait_for "${ELEMENT_TIMEOUT}" 15 "element ${DBAAS_ELEMENT} to become ACTIVE" \
        element_is_active "${DBAAS_ELEMENT}"
}

# --- data inside the element -------------------------------------------------

pg_instance() { # <dbaas api url>
    api_get "$1" "/v1/types/postgres/instances/?name=${MARKER_PG_INSTANCE}" | jq -er '.[0]'
}

pg_instance_is_active() { # <dbaas api url>
    [[ "$(pg_instance "$1" 2>/dev/null | jq -r '.status // "MISSING"')" == "ACTIVE" ]]
}

pg_instance_address() { # <dbaas api url>
    pg_instance "$1" | jq -er '.ipsv4[0]'
}

psql_marker() { # <host> <sql>
    PGPASSWORD="${MARKER_PG_PASSWORD}" psql \
        --host "$1" --username "${MARKER_PG_USER}" --dbname "${MARKER_PG_DATABASE}" \
        --no-align --tuples-only --quiet --set ON_ERROR_STOP=1 --command "$2"
}

pg_is_reachable() { # <host>
    psql_marker "$1" 'SELECT 1' >/dev/null 2>&1
}

seed_element_data() {
    local api_url version instance_uuid user_uuid host
    api_url="$(dbaas_api_url)"
    log "dbaas API at ${api_url}"

    version="$(api_get "${api_url}" /v1/types/postgres/versions/ | jq -er '.[0].uuid')" ||
        die "dbaas offers no postgres versions"

    log "creating postgres instance ${MARKER_PG_INSTANCE}"
    api_post "${api_url}" /v1/types/postgres/instances/ "$(
        jq -n \
            --arg name "${MARKER_PG_INSTANCE}" \
            --arg project_id "${MARKER_PROJECT_ID}" \
            --arg version "/v1/types/postgres/versions/${version}" \
            '{name: $name, project_id: $project_id, version: $version,
              cpu: 1, ram: 2048, disk_size: 8,
              nodes_number: 1, sync_replica_number: 0}'
    )" >/dev/null

    wait_for "${INSTANCE_TIMEOUT}" 15 "postgres instance to become ACTIVE" \
        pg_instance_is_active "${api_url}"

    instance_uuid="$(pg_instance "${api_url}" | jq -er '.uuid')"

    api_post "${api_url}" "/v1/types/postgres/instances/${instance_uuid}/users/" "$(
        jq -n \
            --arg name "${MARKER_PG_USER}" \
            --arg project_id "${MARKER_PROJECT_ID}" \
            --arg password "${MARKER_PG_PASSWORD}" \
            '{name: $name, project_id: $project_id, password: $password}'
    )" >/dev/null
    user_uuid="$(
        api_get "${api_url}" "/v1/types/postgres/instances/${instance_uuid}/users/" |
            jq -er --arg name "${MARKER_PG_USER}" '.[] | select(.name == $name) | .uuid'
    )"

    api_post "${api_url}" "/v1/types/postgres/instances/${instance_uuid}/databases/" "$(
        jq -n \
            --arg name "${MARKER_PG_DATABASE}" \
            --arg project_id "${MARKER_PROJECT_ID}" \
            --arg owner "/v1/types/postgres/instances/${instance_uuid}/users/${user_uuid}" \
            '{name: $name, project_id: $project_id, owner: $owner}'
    )" >/dev/null

    host="$(pg_instance_address "${api_url}")"
    wait_for 600 10 "postgres at ${host} to accept the marker connection" \
        pg_is_reachable "${host}"

    log "writing the marker row"
    psql_marker "${host}" "
        CREATE TABLE IF NOT EXISTS ${MARKER_TABLE} (
            id serial PRIMARY KEY,
            note text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        INSERT INTO ${MARKER_TABLE} (note) VALUES ('before upgrade');
    " >/dev/null
}

verify_element_data() {
    local api_url host before after
    element_is_active "${DBAAS_ELEMENT}" ||
        die "element ${DBAAS_ELEMENT} is not ACTIVE after the upgrade"

    api_url="$(dbaas_api_url)"
    pg_instance_is_active "${api_url}" ||
        die "postgres instance ${MARKER_PG_INSTANCE} is not ACTIVE after the upgrade"

    host="$(pg_instance_address "${api_url}")"
    before="$(psql_marker "${host}" \
        "SELECT count(*) FROM ${MARKER_TABLE} WHERE note = 'before upgrade'")"
    [[ "${before}" == "1" ]] ||
        die "the pre-upgrade marker row is gone (found ${before} rows)"

    # Count first, then insert, then count again. Asserting the total is 1
    # conflates "the write worked" with "this script has run once", and the
    # second is not what is being tested: re-running `verify` is the ordinary
    # thing to do while diagnosing an upgrade that went wrong, and it used to
    # fail on its own previous run.
    local written
    written="$(psql_marker "${host}" \
        "SELECT count(*) FROM ${MARKER_TABLE} WHERE note = 'after upgrade'")"
    psql_marker "${host}" \
        "INSERT INTO ${MARKER_TABLE} (note) VALUES ('after upgrade')" >/dev/null
    after="$(psql_marker "${host}" \
        "SELECT count(*) FROM ${MARKER_TABLE} WHERE note = 'after upgrade'")"
    [[ "${after}" == "$((written + 1))" ]] ||
        die "cannot write to the database after the upgrade (${written} rows before the insert, ${after} after)"

    log "element data survived and the database still accepts writes"
}

# --- entry point -------------------------------------------------------------

case "${1:?usage: workload.sh seed|verify}" in
seed)
    seed_core_data
    seed_element
    seed_element_data
    log "workload is in place"
    ;;
verify)
    verify_core_data
    verify_element_data
    log "workload survived the upgrade"
    ;;
*)
    die "unknown command: $1"
    ;;
esac
