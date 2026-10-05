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

# Write the OpenAPI documents the core element publishes. The user one is
# taken as an artifact, so that an installation can extend its manifest
# schema with the API of the core it talks to; the documents are generated
# rather than committed, so the build has to write them first.

set -eu
set -x
set -o pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

if [[ -n "${LOCAL_GENESIS_SDK_PATH:-}" ]]; then
    # Resolve before tox changes its working directory; preserve spaces in paths.
    LOCAL_GENESIS_SDK_PATH="$(cd "$LOCAL_GENESIS_SDK_PATH" && pwd)"
    export LOCAL_GENESIS_SDK_PATH
fi

tox -e openapi_artifacts
