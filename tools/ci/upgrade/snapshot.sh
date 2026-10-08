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
# Dump the parts of the installation the upgrade must not damage.
#
# Usage: snapshot.sh <directory>
#
# The files are compared by compare-snapshots.sh and uploaded as job artifacts,
# so that a failure can be diagnosed without re-running the whole job.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tools/ci/upgrade/lib.sh
source "${SCRIPT_DIR}/lib.sh"

target_dir="${1:?usage: snapshot.sh <directory>}"
mkdir -p "${target_dir}"

dump() { # <file> <api path>
    core_get "$2" | jq -S '.' >"${target_dir}/$1"
}

dump elements.json /v1/em/elements/
dump target_resources.json /v1/ua/target_resources/
dump resources.json /v1/ua/resources/
dump agents.json /v1/ua/agents/
dump nodes.json /v1/compute/nodes/
dump projects.json /v1/iam/projects/
dump organizations.json /v1/iam/organizations/

jq -n \
    --slurpfile elements "${target_dir}/elements.json" \
    --slurpfile targets "${target_dir}/target_resources.json" \
    --slurpfile actual "${target_dir}/resources.json" \
    --slurpfile nodes "${target_dir}/nodes.json" \
    '{
        elements: ($elements[0] | length),
        target_resources: ($targets[0] | length),
        resources: ($actual[0] | length),
        nodes: ($nodes[0] | length)
    }' >"${target_dir}/counts.json"

log "snapshot written to ${target_dir}: $(tr -d '\n ' <"${target_dir}/counts.json")"
