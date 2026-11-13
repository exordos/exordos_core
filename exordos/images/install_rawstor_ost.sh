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

set -euo pipefail

CORE_PATH=/opt/exordos_core
AGENT_PYTHON=/opt/universal_agent/.venv/bin/python
RAWSTOR_VERSION=${RAWSTOR_VERSION:-99.0.0}
RAWSTOR_WHEEL_VERSION=${RAWSTOR_WHEEL_VERSION:-$RAWSTOR_VERSION}
PACKAGES="$CORE_PATH/exordos/images/rawstor"
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y \
    lvm2 git python3-dev build-essential libjemalloc2

if [[ "$RAWSTOR_VERSION" == 99.0.0 ]]; then
    (cd "$PACKAGES" && sha256sum --check SHA256SUMS)
else
    PACKAGES=$(mktemp -d)
    for package in librawstor rawstor-ost; do
        curl -fsSLo "$PACKAGES/${package}_${RAWSTOR_VERSION}_amd64.deb" \
            "https://github.com/rawstor/librawstor/releases/download/v${RAWSTOR_VERSION}/${package}_${RAWSTOR_VERSION}_amd64.deb"
    done
    curl -fsSLo "$PACKAGES/rawstor-${RAWSTOR_WHEEL_VERSION}-cp39-abi3-manylinux1_x86_64.manylinux_2_5_x86_64.whl" \
        "https://github.com/rawstor/librawstor/releases/download/v${RAWSTOR_VERSION}/rawstor-${RAWSTOR_WHEEL_VERSION}-cp39-abi3-manylinux1_x86_64.manylinux_2_5_x86_64.whl"
fi
sudo env DEBIAN_FRONTEND=noninteractive apt-get install --reinstall -y \
    "$PACKAGES/librawstor_${RAWSTOR_VERSION}_amd64.deb" \
    "$PACKAGES/rawstor-ost_${RAWSTOR_VERSION}_amd64.deb"
# OST instances are configured and started by the storage agent.

# Keep the storage driver on the same SDK revision as core.
if [[ -d /opt/gcl_sdk ]]; then
    sudo "$AGENT_PYTHON" -m pip install /opt/gcl_sdk
else
    SDK_SOURCE=$(python3 - <<'PYTHON'
import tomllib
from pathlib import Path
project = tomllib.loads(Path("/opt/exordos_core/pyproject.toml").read_text())["project"]
for dependency in project["dependencies"]:
    name, _, source = dependency.partition("@")
    if name.split("[", 1)[0].strip().replace("_", "-") == "gcl-sdk":
        print("gcl-sdk @ " + source.strip())
        break
else:
    raise RuntimeError("Pinned SDK dependency not found")
PYTHON
)
    sudo "$AGENT_PYTHON" -m pip install "$SDK_SOURCE"
fi
sudo "$AGENT_PYTHON" -m pip install --force-reinstall --no-deps "$PACKAGES"/rawstor-*.whl

sudo install -m 0755 "$CORE_PATH/exordos/images/ost/prepare.py" /usr/local/sbin/exordos-prepare-ost
sudo install -m 0644 "$CORE_PATH/exordos/images/ost/prepare.service" /etc/systemd/system/exordos-prepare-ost@.service
# LVM needs metadata backups and runtime locks inside the OST unit sandbox.
sudo mkdir -p /etc/systemd/system/rawstor-ost@.service.d
sudo tee /etc/systemd/system/rawstor-ost@.service.d/lvm.conf >/dev/null <<'EOF'
[Unit]
Requires=exordos-prepare-ost@%i.service
After=exordos-prepare-ost@%i.service

[Service]
ReadWritePaths=/etc/lvm /run
EOF
sudo python3 - <<'PY'
import configparser
from pathlib import Path
path = Path('/etc/exordos_universal_agent/exordos_universal_agent.conf')
config = configparser.ConfigParser()
config.read(path)
drivers = [x.strip() for x in config['universal_agent']['caps_drivers'].split(',') if x.strip()]
if 'StorageNodeAgentDriver' not in drivers:
    drivers.append('StorageNodeAgentDriver')
config['universal_agent']['caps_drivers'] = ','.join(drivers)
config['StorageNodeAgentDriver'] = {'meta_file': '/var/lib/exordos/universal_agent/storage_node_meta.json'}
with path.open('w') as stream:
    config.write(stream)
PY
sudo systemctl daemon-reload
