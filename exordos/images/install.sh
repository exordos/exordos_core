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
  openvswitch-switch \
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

# In the dev mode the gcl_sdk package is installed from the local machine
if [[ "$SDK_DEV_MODE" == "true" ]]; then
    uv pip uninstall gcl_sdk
    uv pip install -e "$DEV_SDK_PATH"
fi

# Configuration for universal agent
sudo cp "$GC_PATH/etc/exordos_universal_agent/logging.yaml" /etc/exordos_universal_agent/

# --- ovs_evpn network fabric (gobgpd + evpn_connector) -------------------
# OVS is installed above (openvswitch-switch). The universal agent's evpn_node
# capability renders /etc/gobgp.conf and /opt/evpn/evpn_connector.cfg at runtime
# and (re)starts the services; here we only lay down the binaries, the venv and
# the units. The units are intentionally left disabled — the agent enables and
# starts them once an ovs_evpn network is actually in use.
sudo mkdir -p /opt/evpn/vm_conf
sudo cp "$GC_PATH/etc/evpn/gobgpd.service" /etc/systemd/system/gobgpd.service
sudo cp "$GC_PATH/etc/evpn/evpn-connector.service" /etc/systemd/system/evpn-connector.service

# gobgpd/gobgp binaries. Fetched here with curl -L rather than as a build
# artifact because the GitHub release URL 302-redirects to a signed asset host,
# which the build's http-dep fetcher does not follow. GOBGP_URL may point at a
# local mirror. A fabric image without gobgpd is broken, so a failed fetch
# fails the build (a GitHub 503 once shipped an image with no RR daemon).
# gobgp 4: evpn_connector speaks the v4 gRPC API, so the daemon and the
# connector move together — a v3 gobgpd will not answer it.
GOBGP_URL="${GOBGP_URL:-https://github.com/osrg/gobgp/releases/download/v4.7.0/gobgp_4.7.0_linux_amd64.tar.gz}"
# sha256 of the v4.7.0 linux/amd64 release tarball. Verifying it pins the
# binary by content, so a tampered mirror or a MITM of the download cannot land
# an arbitrary gobgpd that then runs as root. Bump alongside the version above.
GOBGP_SHA256="05d98ca0d7bbcb2f50a6b7b6ee51c5e5b5fd64d6a310ee807040ed9d7104d5e0"
GOBGP_TMP="$(mktemp -d)"
curl -fsSL --retry 5 --retry-all-errors -o "$GOBGP_TMP/gobgp.tar.gz" "$GOBGP_URL"
echo "${GOBGP_SHA256}  ${GOBGP_TMP}/gobgp.tar.gz" | sha256sum -c -
tar -xf "$GOBGP_TMP/gobgp.tar.gz" -C "$GOBGP_TMP"
sudo install -m 0755 "$GOBGP_TMP/gobgpd" "$GOBGP_TMP/gobgp" /usr/local/bin/
rm -rf "$GOBGP_TMP"
test -x /usr/local/bin/gobgpd

# evpn_connector runs on the image's own Python: since it moved to the gobgp
# v4 API it no longer pins cp38-only wheels, so it needs no interpreter of its
# own. Only built when the source is provided at build time
# (LOCAL_EVPN_CONNECTOR_PATH -> /opt/evpn_connector), mirroring the optional
# SDK dev path above.
EVPN_SRC="/opt/evpn_connector"
if [[ -d "$EVPN_SRC" ]]; then
    sudo chown -R ubuntu:ubuntu /opt/evpn
    python3 -m venv /opt/evpn/venv
    # The distro's bundled pip cannot parse some of the dependency metadata.
    /opt/evpn/venv/bin/pip install -q --upgrade pip
    # pbr needs a version when there is no git metadata; setuptools provides
    # pkg_resources at runtime in the otherwise-bare venv.
    PBR_VERSION=0.0.1 /opt/evpn/venv/bin/pip install -q \
        setuptools "$EVPN_SRC"
    # The venv is built as ubuntu (uv writes there), but evpn-connector.service
    # runs it as root. Leaving /opt/evpn ubuntu-owned would let a local ubuntu
    # user replace the binary and get root execution — hand it back to root now
    # that the build-time writes are done.
    sudo chown -R root:root /opt/evpn
fi

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
