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
# Resolve the version pair for the upgrade test and make sure the base release
# can actually be installed.
#
#   NEW  - the version this run built, taken from the build artifacts.
#   PREV - the newest release tag that is not NEW; this is what an existing
#          installation is expected to be running.
#
# Usage: resolve-versions.sh <path-to-version.txt>
#
# Writes prev/new to $GITHUB_OUTPUT when running under Actions, and always
# prints them.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tools/ci/upgrade/lib.sh
source "${SCRIPT_DIR}/lib.sh"

ELEMENTS_REPO_URL="${ELEMENTS_REPO_URL:-https://repo.exordos.com/exordos-elements}"
CORE_IMAGE_NAME="${CORE_IMAGE_NAME:-exordos-core.raw.zst}"

version_file="${1:?usage: resolve-versions.sh <path-to-version.txt>}"

new_version="$(tr -d '[:space:]' <"${version_file}")"
[[ -n "${new_version}" ]] || die "${version_file} is empty"

# `git tag --sort=-v:refname` orders newest first; the pattern keeps release
# tags only, so the `backup/*` tags cannot win.
prev_version="$(
    git tag --list --sort=-v:refname |
        grep -Ex '[0-9]+\.[0-9]+\.[0-9]+' |
        grep -vFx "${new_version}" |
        head -n 1
)" || true

[[ -n "${prev_version}" ]] ||
    die "no release tag to upgrade from (fetch tags: actions/checkout needs fetch-tags)"

# The built version is the nearest reachable tag with its patch bumped, so it
# should always outrank every existing tag. When it does not, the build was
# labelled from a history that was missing tags — a shallow or tagless fetch,
# or a self-hosted runner reusing an old working directory — and upgrading to
# it would run the test backwards. Say which tags were visible, because that is
# the evidence needed to tell a mislabelled build from a genuinely odd branch.
if [[ "$(printf '%s\n%s\n' "${prev_version}" "${new_version}" | sort -V | head -n 1)" != "${prev_version}" ]]; then
    log "release tags visible here: $(
        git tag --list --sort=-v:refname | grep -Ex '[0-9]+\.[0-9]+\.[0-9]+' | head -n 5 | tr '\n' ' '
    )"
    log "the built version comes from the nearest tag reachable at build time, patch + 1"
    die "the newest release tag ${prev_version} is newer than the built version ${new_version}"
fi

# The image of the base release has to exist, otherwise the job would fail
# later inside bootstrap with a much less obvious error.  This deliberately
# fails the job instead of skipping: a silent skip turns the whole test into a
# green light that checks nothing.
base_image_url="${ELEMENTS_REPO_URL}/core/${prev_version}/images/${CORE_IMAGE_NAME}"
curl -fsS --head "${base_image_url}" >/dev/null ||
    die "base release image is not published: ${base_image_url}"

log "upgrading ${prev_version} -> ${new_version}"

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    {
        printf 'prev=%s\n' "${prev_version}"
        printf 'new=%s\n' "${new_version}"
    } >>"${GITHUB_OUTPUT}"
fi

printf 'prev=%s new=%s\n' "${prev_version}" "${new_version}"
