#!/usr/bin/env bash
# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License").

set -euo pipefail

DESTINATION=$1
shift
RAWSTOR_VERSION=${RAWSTOR_VERSION:-0.3.1}
RAWSTOR_WHEEL_VERSION=${RAWSTOR_WHEEL_VERSION:-$RAWSTOR_VERSION}
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
RELEASE_URL="https://github.com/rawstor/librawstor/releases/download/v${RAWSTOR_VERSION}"
mkdir -p "$DESTINATION"
for package in "$@"; do
    filename="${package}_${RAWSTOR_VERSION}_amd64.deb"
    curl -fLsS --retry 3 "$RELEASE_URL/$filename" -o "$DESTINATION/$filename"
done
filename="rawstor-${RAWSTOR_WHEEL_VERSION}-cp39-abi3-manylinux1_x86_64.manylinux_2_5_x86_64.whl"
curl -fLsS --retry 3 "$RELEASE_URL/$filename" -o "$DESTINATION/$filename"

if [[ "$RAWSTOR_VERSION" == 0.3.1 && "$RAWSTOR_WHEEL_VERSION" == 0.3.1 ]]; then
    (cd "$DESTINATION" && sha256sum --check --ignore-missing "$SCRIPT_DIR/rawstor/SHA256SUMS")
fi
