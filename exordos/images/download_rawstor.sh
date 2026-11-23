#!/usr/bin/env bash
# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License").

set -euo pipefail

DESTINATION=$1
shift
RAWSTOR_VERSION=${RAWSTOR_VERSION:-0.3.3}
RAWSTOR_WHEEL_VERSION=${RAWSTOR_WHEEL_VERSION:-$RAWSTOR_VERSION}
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
RELEASE_URL="https://github.com/rawstor/librawstor/releases/download/v${RAWSTOR_VERSION}"
mkdir -p "$DESTINATION"
files=()
for package in "$@"; do
    files+=("${package}_${RAWSTOR_VERSION}_amd64.deb")
done
files+=("rawstor-${RAWSTOR_WHEEL_VERSION}-cp39-abi3-manylinux1_x86_64.manylinux_2_5_x86_64.whl")
# Require a committed digest for every asset, including version overrides.
for filename in "${files[@]}"; do
    digest=$(awk -v name="$filename" '$2 == name { print $1 }' "$SCRIPT_DIR/rawstor/SHA256SUMS")
    if [[ ! "$digest" =~ ^[0-9a-f]{64}$ ]]; then
        echo "No trusted SHA-256 for $filename; update SHA256SUMS before overriding versions" >&2
        exit 1
    fi
    curl -fLsS --retry 3 "$RELEASE_URL/$filename" -o "$DESTINATION/$filename"
    (cd "$DESTINATION" && printf '%s  %s\n' "$digest" "$filename" | sha256sum --check -)
done
