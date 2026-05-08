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

DEV_SDK_PATH="/opt/gcl_sdk"
SDK_DEV_MODE=$([ -d "$DEV_SDK_PATH" ] && echo "true" || echo "false")

# Install packages
sudo apt update
sudo apt install yq postgresql-common libev-dev libvirt-dev \
    tftpd-hpa nginx-full isc-dhcp-server curl iptables-persistent -y

# Install PostgreSQL $PG_VERSION
sudo YES=1 /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh
sudo apt update
sudo apt install postgresql-"$PG_VERSION" -y

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
    echo "0 * * * * root journalctl --vacuum-size=500M" | sudo tee /etc/cron.d/genesis_vacuum_logs > /dev/null
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
    echo "1 * * * * root systemctl start logrotate" | sudo tee -a /etc/cron.d/genesis_vacuum_logs > /dev/null
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
sudo mkdir -p /etc/nginx/genesis/

# Cert to restrict default_server
sudo openssl req -x509 -nodes -days 3650 -newkey rsa:2048 -subj "/C=PE/ST=Exordos/L=Exordos/O=Exordos core dummy cert. /OU=IT Department/CN=exordos.core" -keyout /etc/nginx/ssl/nginx.key -out /etc/nginx/ssl/nginx.crt

# Block any connections not explicitly set
sudo rm -f /etc/nginx/sites-enabled/default
sudo cp "$GC_PATH/etc/nginx/sites-available/default" /etc/nginx/sites-available/default
sudo ln -s /etc/nginx/sites-available/default /etc/nginx/sites-enabled/default

cat <<EOF | sudo tee -a /etc/nginx/nginx.conf
include /etc/nginx/genesis/*.conf;
EOF

# Add web interface
sudo rm -fr /var/www/html
sudo tar -xf "$GC_ART_DIR/html.tgz" -C /var/www/
sudo chown -R www-data:www-data /var/www/html

sudo cp "$GC_PATH/etc/nginx/sites-available/exordos.conf" /etc/nginx/sites-available/exordos.conf
sudo ln -s /etc/nginx/sites-available/exordos.conf /etc/nginx/sites-enabled/exordos.conf
sudo systemctl enable nginx

# Install exordos core
sudo mkdir -p $GC_CFG_DIR
sudo cp "$GC_PATH/etc/exordos_core/core_agent.conf" $GC_CFG_DIR/
sudo cp "$GC_PATH/etc/exordos_core/logging.yaml" $GC_CFG_DIR/
sudo cp "$GC_PATH/etc/exordos_core/event_type_mapping.yaml" $GC_CFG_DIR/
sudo cp "$GC_PATH/genesis/images/bootstrap.sh" $BOOTSTRAP_PATH/0100-ec-bootstrap.sh

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
sudo cp "$GC_PATH/etc/genesis_universal_agent/logging.yaml" /etc/genesis_universal_agent/

# Apply migrations
# The migrations are applied in the bootstrap script as well.
# It's required for update the core otherwise the migrations won't be applied on the update.
# It's fine to apply migrations here as:
# 1) The bootstrap script will transfer the data to the data disk
# 2) It's speed up the first run since the migrations are already applied.
# 3) It's allows to debug the migrations at build time.
ra-apply-migration --config-dir "$GC_PATH/etc/exordos_core/" --path "$GC_PATH/migrations"

deactivate

# Install CLI
curl -fsSL https://repository.genesis-core.tech/install.sh | sudo sh

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
sudo ln -sf "$VENV_PATH/bin/genesis-universal-agent" "/usr/bin/genesis-universal-agent"
sudo ln -sf "$VENV_PATH/bin/genesis-universal-agent-db-back" "/usr/bin/genesis-universal-agent-db-back"
sudo ln -sf "$VENV_PATH/bin/genesis-universal-scheduler" "/usr/bin/genesis-universal-scheduler"
sudo ln -sf "$VENV_PATH/bin/genesis-ci" "/usr/bin/gctl"

# Install Systemd service files
# The exordos services are enabled in the bootstrap
# script only after database is ready
sudo cp "$GC_PATH/etc/systemd/ec-user-api.service" $SYSTEMD_SERVICE_DIR
sudo cp "$GC_PATH/etc/systemd/ec-boot-api.service" $SYSTEMD_SERVICE_DIR
sudo cp "$GC_PATH/etc/systemd/ec-orch-api.service" $SYSTEMD_SERVICE_DIR
sudo cp "$GC_PATH/etc/systemd/ec-status-api.service" $SYSTEMD_SERVICE_DIR
sudo cp "$GC_PATH/etc/systemd/ec-gservice.service" $SYSTEMD_SERVICE_DIR
sudo cp "$GC_PATH/etc/systemd/ec-core-agent.service" $SYSTEMD_SERVICE_DIR
sudo cp "$GC_PATH/etc/systemd/genesis-universal-agent.service" $SYSTEMD_SERVICE_DIR
sudo cp "$GC_PATH/etc/systemd/genesis-universal-scheduler.service" $SYSTEMD_SERVICE_DIR

# Prepare DNSaaS

# Install packages
sudo apt install pdns-backend-pgsql pdns-server dnsdist -y

#pdns
sudo rm /etc/powerdns/pdns.d/bind.conf
sudo cp "$GC_PATH/etc/powerdns/exordos.conf" /etc/powerdns/pdns.d/exordos.conf
sudo systemctl enable pdns

#dnsdist

# Optional, only for public resolving, for ex. ACME dns01 certs challenge
sudo cp "$GC_PATH/etc/dnsdist/dnsdist-public.conf" /etc/dnsdist/dnsdist-public.conf
sudo systemctl enable dnsdist@public
sudo systemctl enable dnsdist@private

# Set local IP where needed
# LOCAL_IP=$(cat "$GC_PATH/genesis/images/startup_cfg.yaml" | yq '.startup_entities.core_ip' -r)
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
