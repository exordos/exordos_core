#!/usr/bin/env bash

# Copyright 2025 Genesis Corporation
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

set -eu
set -x
set -o pipefail

GC_PATH="/opt/exordos_core"
GC_CFG_DIR=/etc/exordos_core
GC_ART_DIR="$GC_PATH/artifacts"
VENV_PATH="$GC_PATH/.venv"
BOOTSTRAP_PATH="/var/lib/exordos/bootstrap/scripts"

PG_VERSION="18"
GC_PG_USER="exordos_core"
GC_PG_PASS="exordos_core"
GC_PG_DB="exordos_core"

SYSTEMD_SERVICE_DIR=/etc/systemd/system/
IAM_CACHE_GO_VERSION="1.23.12"

DEV_SDK_PATH="/opt/gcl_sdk"
SDK_DEV_MODE=$([ -d "$DEV_SDK_PATH" ] && echo "true" || echo "false")

# Install packages
sudo apt update
sudo apt install \
  yq curl \
  libev-dev libvirt-dev \
  postgresql-common postgresql-"$PG_VERSION" \
  tftpd-hpa nginx-full isc-dhcp-server iptables-persistent \
  pdns-backend-pgsql pdns-server dnsdist \
  -y
# Install PostgreSQL $PG_VERSION
#sudo YES=1 /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh
#sudo apt update
#sudo apt install postgresql-"$PG_VERSION" -y

# Configure PostgreSQL
sudo -u postgres psql -c "ALTER SYSTEM SET io_method = 'io_uring';"
# It's fine to create the user and database here since the bootstrap will transfer
# the data to the data disk
sudo -u postgres psql -c "CREATE ROLE $GC_PG_USER WITH LOGIN PASSWORD '$GC_PG_PASS';"
sudo -u postgres psql -c "CREATE DATABASE $GC_PG_USER OWNER $GC_PG_DB;"

# Configure SSH
ALLOW_USER_PASSWD=${ALLOW_USER_PASSWD-}
if [ -n "$ALLOW_USER_PASSWD" ]; then
    echo "ubuntu:ubuntu" | sudo chpasswd
    sudo rm /etc/ssh/sshd_config.d/60-cloudimg-settings.conf
    sudo yq -yi '.system_info.default_user.lock_passwd |= false' /etc/cloud/cloud.cfg
fi

FREQUENT_LOG_VACUUM=${FREQUENT_LOG_VACUUM-}
if [ -n "$FREQUENT_LOG_VACUUM" ]; then
    # Optimize log rotation
    echo "0 * * * * root journalctl --vacuum-size=500M" | sudo tee /etc/cron.d/exordos_vacuum_logs > /dev/null
    cat <<EOF | sudo tee /etc/logrotate.d/rsyslog > /dev/null
/var/log/syslog
/var/log/mail.log
/var/log/kern.log
/var/log/auth.log
/var/log/user.log
/var/log/cron.log
{
        rotate 5
        hourly
        size 100M
        missingok
        notifempty
        compress
        delaycompress
        sharedscripts
        postrotate
                /usr/lib/rsyslog/rsyslog-rotate
        endscript
}
EOF
    echo "1 * * * * root systemctl start logrotate" | sudo tee -a /etc/cron.d/exordos_vacuum_logs > /dev/null
fi

# Useful for all-in-one-vm tests
# sudo apt install qemu-kvm libvirt-daemon-system zfsutils-linux \
#     libvirt-daemon-driver-storage-zfs libvirt-clients -y
#
# # Add disk, create pool
# zpool create zfspool /dev/vdX
#
# # Just separate dataset for libvirt
# zfs create zfspool/disks
#
# # Add pool into libvirt
# virsh pool-define-as --name zfspool --source-name zfspool/disks --type zfs
# virsh pool-start zfspool


# Configure netboot
sudo mkdir -p /srv/tftp/bios
sudo cp "$GC_ART_DIR/undionly.kpxe" /srv/tftp/bios/undionly.kpxe
sudo cp "$GC_ART_DIR/initrd.img" /srv/tftp/bios/initrd.img
sudo cp "$GC_ART_DIR/vmlinuz" /srv/tftp/bios/vmlinuz

# Prepare nginx for LB
sudo mkdir -p /etc/nginx/ssl
sudo chown www-data:www-data /etc/nginx/ssl
sudo mkdir -p /etc/nginx/exordos/

# Cert to restrict default_server
sudo openssl req -x509 -nodes -days 3650 -newkey rsa:2048 -subj "/C=PE/ST=Exordos/L=Exordos/O=Exordos core dummy cert. /OU=IT Department/CN=exordos.core" -keyout /etc/nginx/ssl/nginx.key -out /etc/nginx/ssl/nginx.crt

# Block any connections not explicitly set
sudo rm -f /etc/nginx/sites-enabled/default
sudo cp "$GC_PATH/etc/nginx/sites-available/default" /etc/nginx/sites-available/default
sudo ln -s /etc/nginx/sites-available/default /etc/nginx/sites-enabled/default

cat <<EOF | sudo tee -a /etc/nginx/nginx.conf
include /etc/nginx/exordos/*.conf;
EOF

# comment in /etc/nginx/nginx.conf line with server_tokens
sudo sed -i 's/server_tokens/# server_tokens/' /etc/nginx/nginx.conf

sudo cp "$GC_PATH/etc/nginx/sites-available/exordos.conf" /etc/nginx/sites-available/exordos.conf
sudo ln -s /etc/nginx/sites-available/exordos.conf /etc/nginx/sites-enabled/exordos.conf
sudo systemctl enable nginx

# Install exordos core
sudo mkdir -p $GC_CFG_DIR
sudo cp "$GC_PATH/etc/exordos_core/logging.yaml" $GC_CFG_DIR/
sudo install \
  -o root \
  -g root \
  -m 0644 \
  "$GC_PATH/etc/exordos_core/iam_cache.json.example" \
  "$GC_CFG_DIR/iam_cache.json"
# Drop-in config dir loaded by ec-user-api via --config-dir. The notification
# element lands its [events] override and event_type_mapping.yaml here; must
# exist (oslo --config-dir errors on a missing directory).
sudo mkdir -p $GC_CFG_DIR/exordos_core.d
sudo cp "$GC_PATH/exordos/images/bootstrap.sh" $BOOTSTRAP_PATH/0100-ec-bootstrap.sh

# Build the IAM cache with a temporary Go toolchain. Only the stripped static
# binary is installed into the image; the toolchain and every build cache are
# removed both after a successful build and if the build fails.
case "$(dpkg --print-architecture)" in
  amd64)
    IAM_CACHE_GO_ARCH="amd64"
    IAM_CACHE_GO_SHA256="d3847fef834e9db11bf64e3fb34db9c04db14e068eeb064f49af747010454f90"
    ;;
  arm64)
    IAM_CACHE_GO_ARCH="arm64"
    IAM_CACHE_GO_SHA256="52ce172f96e21da53b1ae9079808560d49b02ac86cecfa457217597f9bc28ab3"
    ;;
  *)
    echo "Unsupported architecture for the IAM cache: $(dpkg --print-architecture)" >&2
    exit 1
    ;;
esac

IAM_CACHE_BUILD_DIR=$(mktemp -d)
cleanup_iam_cache_build() {
    if [[ -n "${IAM_CACHE_BUILD_DIR:-}" && -d "$IAM_CACHE_BUILD_DIR" ]]; then
        rm -rf -- "$IAM_CACHE_BUILD_DIR"
    fi
}
trap cleanup_iam_cache_build EXIT

curl -fsSLo "$IAM_CACHE_BUILD_DIR/go.tar.gz" \
  "https://go.dev/dl/go${IAM_CACHE_GO_VERSION}.linux-${IAM_CACHE_GO_ARCH}.tar.gz"
echo "$IAM_CACHE_GO_SHA256  $IAM_CACHE_BUILD_DIR/go.tar.gz" \
  | sha256sum --check -
tar -xzf "$IAM_CACHE_BUILD_DIR/go.tar.gz" -C "$IAM_CACHE_BUILD_DIR"

(
    cd "$GC_PATH/services/iam-cache"
    CGO_ENABLED=0 \
    GOCACHE="$IAM_CACHE_BUILD_DIR/go-cache" \
    GOPATH="$IAM_CACHE_BUILD_DIR/gopath" \
      "$IAM_CACHE_BUILD_DIR/go/bin/go" build \
        -buildvcs=false \
        -trimpath \
        -ldflags="-s -w" \
        -o "$IAM_CACHE_BUILD_DIR/exordos-iam-cache" \
        ./cmd/exordos-iam-cache
)
sudo install \
  -o root \
  -g root \
  -m 0755 \
  "$IAM_CACHE_BUILD_DIR/exordos-iam-cache" \
  /usr/bin/exordos-iam-cache

cleanup_iam_cache_build
trap - EXIT
unset IAM_CACHE_BUILD_DIR

cd "$GC_PATH"
uv sync
source "$GC_PATH"/.venv/bin/activate

sudo chown -R ubuntu:ubuntu "$GC_PATH"

# In the dev mode the gcl_sdk package is installed from the local machine.
# Reinstall with the same extras pyproject.toml declares (uv sync's normal
# extra resolution doesn't apply to this manual editable install), or
# libvirt-python silently goes missing and PoolAgentDriver can't be built.
if [[ "$SDK_DEV_MODE" == "true" ]]; then
    uv pip uninstall gcl_sdk
    uv pip install -e "$DEV_SDK_PATH[libvirt]"
fi

# Configuration for universal agent
sudo cp "$GC_PATH/etc/exordos_universal_agent/logging.yaml" /etc/exordos_universal_agent/

# Apply migrations
# The migrations are applied in the bootstrap script as well.
# It's required for update the core otherwise the migrations won't be applied on the update.
# It's fine to apply migrations here as:
# 1) The bootstrap script will transfer the data to the data disk
# 2) It's speed up the first run since the migrations are already applied.
# 3) It's allows to debug the migrations at build time.
GCL_SDK_MIGRATIONS_PATH=$(python3 -c "import os, gcl_sdk.migrations as m; print(os.path.dirname(m.__file__))")
OS_DB__CONNECTION_URL="postgresql://$GC_PG_USER:$GC_PG_PASS@127.0.0.1:5432/$GC_PG_DB" ra-apply-migration --path "$GCL_SDK_MIGRATIONS_PATH"
OS_DB__CONNECTION_URL="postgresql://$GC_PG_USER:$GC_PG_PASS@127.0.0.1:5432/$GC_PG_DB" ra-apply-migration --path "$GC_PATH/migrations"

deactivate

# Install CLI
curl -fsSL https://repo.exordos.com/install.sh | sh

# Misc config
# Disable DHCP for the main interface, it will be configured in the bootstrap script
sudo cp "$GC_PATH/etc/90-exordos-dummy-config.yaml" /etc/netplan/90-exordos-net-base-config.yaml


# Create links to venv
sudo ln -sf "$VENV_PATH/bin/ec-user-api" "/usr/bin/ec-user-api"
sudo ln -sf "$VENV_PATH/bin/ec-boot-api" "/usr/bin/ec-boot-api"
sudo ln -sf "$VENV_PATH/bin/ec-orch-api" "/usr/bin/ec-orch-api"
sudo ln -sf "$VENV_PATH/bin/ec-status-api" "/usr/bin/ec-status-api"
sudo ln -sf "$VENV_PATH/bin/ec-gservice" "/usr/bin/ec-gservice"
sudo ln -sf "$VENV_PATH/bin/ec-bootstrap" "/usr/bin/ec-bootstrap"
sudo ln -sf "$VENV_PATH/bin/ec-bootstrap-templates" "/usr/bin/ec-bootstrap-templates"
sudo ln -sf "$VENV_PATH/bin/exordos-universal-agent" "/usr/bin/exordos-universal-agent"
sudo ln -sf "$VENV_PATH/bin/exordos-universal-agent-db-back" "/usr/bin/exordos-universal-agent-db-back"
sudo ln -sf "$VENV_PATH/bin/exordos-universal-scheduler" "/usr/bin/exordos-universal-scheduler"
sudo ln -sf "$VENV_PATH/bin/exordos-repo-proxy-gservice" "/usr/bin/exordos-repo-proxy-gservice"

# Install Systemd service files
# The exordos services are enabled in the bootstrap
# script only after database is ready
sudo cp "$GC_PATH/etc/systemd/ec-user-api.service" $SYSTEMD_SERVICE_DIR
sudo cp "$GC_PATH/etc/systemd/ec-boot-api.service" $SYSTEMD_SERVICE_DIR
sudo cp "$GC_PATH/etc/systemd/ec-orch-api.service" $SYSTEMD_SERVICE_DIR
sudo cp "$GC_PATH/etc/systemd/ec-status-api.service" $SYSTEMD_SERVICE_DIR
sudo cp "$GC_PATH/etc/systemd/ec-gservice.service" $SYSTEMD_SERVICE_DIR
sudo cp "$GC_PATH/etc/systemd/ec-core-agent.service" $SYSTEMD_SERVICE_DIR
sudo cp "$GC_PATH/etc/systemd/exordos-universal-agent.service" $SYSTEMD_SERVICE_DIR
sudo cp "$GC_PATH/etc/systemd/exordos-universal-scheduler.service" $SYSTEMD_SERVICE_DIR
sudo cp "$GC_PATH/etc/systemd/exordos-repo-proxy-gservice.service" $SYSTEMD_SERVICE_DIR
sudo cp "$GC_PATH/etc/systemd/exordos-iam-cache.service" $SYSTEMD_SERVICE_DIR

# Prepare DNSaaS
sudo systemctl disable --now pdns dnsdist@public dnsdist@private

#pdns
sudo rm /etc/powerdns/pdns.d/bind.conf

#dnsdist

# Optional, only for public resolving, for ex. ACME dns01 certs challenge
sudo cp "$GC_PATH/etc/dnsdist/dnsdist-public.conf" /etc/dnsdist/dnsdist-public.conf

# Set local IP where needed
# LOCAL_IP=$(cat "$GC_PATH/exordos/images/startup_cfg.yaml" | yq '.startup_entities.core_ip' -r)
# Use static IP for now
# LOCAL_IP="10.20.0.2"
# echo "DNS=${LOCAL_IP}" | sudo tee -a /etc/systemd/resolved.conf > /dev/null
# sudo sed -i 's/setLocal("10.20.0.2:53")/setLocal("'"${LOCAL_IP}"':53")/' /etc/dnsdist/dnsdist-private.conf

# Configure Grub to include autonomous mode for the update procedure
cat <<EOF | sudo tee -a /etc/grub.d/40_custom
menuentry "Autonomous update mode" {
    search --no-floppy --set=root --file /srv/tftp/bios/vmlinuz

    linux /srv/tftp/bios/vmlinuz showopts ip=none net.ifnames=0 biosdevname=0 autonomous=1 console=ttyS0,115200

    initrd /srv/tftp/bios/initrd.img
}
EOF
echo "GRUB_TIMEOUT_STYLE=menu" | sudo tee -a /etc/default/grub.d/50-cloudimg-settings.cfg
sudo update-grub

cat <<EOT | sudo tee /etc/motd
▄▖       ▌      ▄▖
▙▖▚▘▛▌▛▘▛▌▛▌▛▘  ▌ ▛▌▛▘█▌
▙▖▞▖▙▌▌ ▙▌▙▌▄▌  ▙▖▙▌▌ ▙▖


Welcome to Exordos Core virtual machine!

All materials can be found here:
https://github.com/exordos

EOT
