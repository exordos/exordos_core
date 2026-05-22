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

# Lightweight migration script for phoenix.core (inner VM)
# Transforms Genesis Core installation to Exordos Core
# For systems that already have PG18 and proper disk layout

set -euo pipefail

# Configuration
OLD_CODE_DIR="/opt/genesis_core"
NEW_CODE_DIR="/opt/exordos_core"
OLD_DATA_DIR="/var/lib/genesis"
NEW_DATA_DIR="/var/lib/exordos"
OLD_ETC_DIR="/etc/genesis_core"
NEW_ETC_DIR="/etc/exordos_core"
OLD_AGENT_ETC_DIR="/etc/genesis_universal_agent"
NEW_AGENT_ETC_DIR="/etc/exordos_universal_agent"

OLD_DB_NAME="genesis_core"
OLD_DB_USER="genesis_core"

BACKUP_DIR="/var/backups"
DB_NAME="exordos_core"
DB_USER="exordos_core"
PG_VERSION="18"
PERSISTENT_MOUNT="/persist"
DATA_DISK="/dev/vdb"

TEMPLATE_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/core_set_spec.json.template"
MANIFEST_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/core.yaml"
SCHEDULER_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scheduler_migration_hack.patch"
IMAGE_PATH="${IMAGE_PATH:-/var/lib/exordos/images/exordos-core.qcow2}"
IMAGE_URI="${IMAGE_URI:-https://repo.exordos.com/exordos-elements/core/0.1.0/images/exordos-core.raw.zst}"

MACHINE_PREFIX=""
NETWORK=""
NODE_UUID=""
PROFILE=""
CORES=""
RAM=""
DISK_ROOT_SIZE=""
DISK_DATA_SIZE=""
PORT_MAIN_MAC=""
PORT_MAIN_IP=""
PORT_BOOT_MAC=""
NETWORK_CIDR=""
BOOT_NETWORK_CIDR=""
POOL_UUID=""
IAM_DEFAULT_CLIENT_ID=""
IAM_DEFAULT_CLIENT_SECRET=""
IAM_DEFAULT_CLIENT_UUID=""

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

if [[ $EUID -ne 0 ]]; then
   log_error "This script must be run as root"
   exit 1
fi

usage() {
    cat << 'USAGE_EOF'
Usage: $0 [OPTIONS]

Required parameters:
    --machine-prefix PREFIX      Machine prefix for VM naming (e.g., "vm-")
    --network NAME               Network name for core node ports
    --node-uuid UUID             VM UUID from virsh dumpxml
    --core NUM                   Number of CPU cores
    --ram MB                     Memory in MB
    --disk-root-size GB          Root disk size in GB
    --disk-data-size GB          Data disk size in GB
    --port-main-mac MAC          Primary port MAC address
    --port-main-ip IP            Primary port IP address
    --port-boot-mac MAC          Secondary/boot port MAC address
    --network-cidr CIDR          Main network CIDR (e.g., 10.70.0.0/16)
    --boot-network-cidr CIDR     Boot network CIDR (e.g., 10.100.0.0/22)
    --admin-password             Admin password
    --profile PROFILE            Configuration profile name
    --pool-uuid UUID             Pool UUID for core set
    --iam-default-client-id ID   IAM default client ID
    --iam-default-client-secret SECRET  IAM default client secret
    --iam-default-client-uuid UUID      IAM default client UUID

Optional parameters:
    --template FILE              Path to template file
    --image-path PATH            Local path to QCOW2 image
    --image-uri URI              URI to Exordos Core image
    -h, --help                   Show this help message
USAGE_EOF
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --machine-prefix) MACHINE_PREFIX="$2"; shift 2 ;;
        --network)        NETWORK="$2";        shift 2 ;;
        --node-uuid)      NODE_UUID="$2";      shift 2 ;;
        --core)           CORES="$2";          shift 2 ;;
        --ram)            RAM="$2";            shift 2 ;;
        --disk-root-size) DISK_ROOT_SIZE="$2"; shift 2 ;;
        --disk-data-size) DISK_DATA_SIZE="$2"; shift 2 ;;
        --port-main-mac)  PORT_MAIN_MAC="$2";  shift 2 ;;
        --port-main-ip)   PORT_MAIN_IP="$2";   shift 2 ;;
        --port-boot-mac)      PORT_BOOT_MAC="$2";      shift 2 ;;
        --network-cidr)      NETWORK_CIDR="$2";      shift 2 ;;
        --boot-network-cidr) BOOT_NETWORK_CIDR="$2"; shift 2 ;;
        --template)          TEMPLATE_FILE="$2";     shift 2 ;;
        --image-path)     IMAGE_PATH="$2";     shift 2 ;;
        --image-uri)      IMAGE_URI="$2";      shift 2 ;;
        --admin-password)          ADMIN_PASSWORD="$2";          shift 2 ;;
        --profile)                PROFILE="$2";                shift 2 ;;
        --pool-uuid)              POOL_UUID="$2";              shift 2 ;;
        --iam-default-client-id)     IAM_DEFAULT_CLIENT_ID="$2";     shift 2 ;;
        --iam-default-client-secret) IAM_DEFAULT_CLIENT_SECRET="$2"; shift 2 ;;
        --iam-default-client-uuid)   IAM_DEFAULT_CLIENT_UUID="$2";   shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) log_error "Unknown parameter: $1"; usage; exit 1 ;;
    esac
done

MISSING=""
[[ -z "$MACHINE_PREFIX" ]] && MISSING="$MISSING --machine-prefix"
[[ -z "$NETWORK" ]]        && MISSING="$MISSING --network"
[[ -z "$NODE_UUID" ]]      && MISSING="$MISSING --node-uuid"
[[ -z "$CORES" ]]          && MISSING="$MISSING --core"
[[ -z "$RAM" ]]            && MISSING="$MISSING --ram"
[[ -z "$DISK_ROOT_SIZE" ]] && MISSING="$MISSING --disk-root-size"
[[ -z "$DISK_DATA_SIZE" ]] && MISSING="$MISSING --disk-data-size"
[[ -z "$PORT_MAIN_MAC" ]]       && MISSING="$MISSING --port-main-mac"
[[ -z "$NETWORK_CIDR" ]]        && MISSING="$MISSING --network-cidr"
[[ -z "$BOOT_NETWORK_CIDR" ]]   && MISSING="$MISSING --boot-network-cidr"
[[ -z "$PORT_MAIN_IP" ]]   && MISSING="$MISSING --port-main-ip"
[[ -z "$PORT_BOOT_MAC" ]]  && MISSING="$MISSING --port-boot-mac"
[[ -z "$ADMIN_PASSWORD" ]]          && MISSING="$MISSING --admin-password"
[[ -z "$PROFILE" ]]                 && MISSING="$MISSING --profile"
[[ -z "$POOL_UUID" ]]               && MISSING="$MISSING --pool-uuid"
[[ -z "$IAM_DEFAULT_CLIENT_ID" ]]     && MISSING="$MISSING --iam-default-client-id"
[[ -z "$IAM_DEFAULT_CLIENT_SECRET" ]] && MISSING="$MISSING --iam-default-client-secret"
[[ -z "$IAM_DEFAULT_CLIENT_UUID" ]]   && MISSING="$MISSING --iam-default-client-uuid"

if [[ -n "$MISSING" ]]; then
    log_error "Missing required parameters:$MISSING"
    usage
    exit 1
fi

if [[ ! -f "$TEMPLATE_FILE" ]]; then
    log_error "Template file not found: $TEMPLATE_FILE"
    exit 1
fi

log_info "Starting Genesis Core -> Exordos Core migration"

# Step 1: Disable all Genesis Core services
log_info "Step 1: Disable Genesis services..."
systemctl disable --now genesis-universal-agent genesis-universal-scheduler 2>/dev/null || true
systemctl disable --now gc-user-api gc-orch-api gc-boot-api gc-status-api gc-core-agent 2>/dev/null || true
sleep 2
log_info "Services disabled"

# Step 2: Deploy new code
log_info "Step 2: Deploying Exordos Core code..."
if [[ -d "$NEW_CODE_DIR" ]]; then
    log_warn "Exordos Core directory already exists"
fi
log_info "NOTE: Exordos Core code should be deployed to $NEW_CODE_DIR"
log_info "      This is a placeholder step - actual code deployment needed"

# Step 3: Migrate data directories
log_info "Step 3: Migrating data directories..."
if [[ -d "$OLD_DATA_DIR" && ! -d "$NEW_DATA_DIR" ]]; then
    cp -r "$OLD_DATA_DIR" "$NEW_DATA_DIR"
    log_info "Data migrated to $NEW_DATA_DIR"
fi

# Step 4: Migrate configuration files
log_info "Step 4: Migrating configuration files..."

mkdir -p "$NEW_ETC_DIR"
mkdir -p "$NEW_ETC_DIR/exordos_core.d"
if [[ -d "$OLD_ETC_DIR" ]]; then
    for file in "$OLD_ETC_DIR"/*; do
        if [[ -f "$file" ]]; then
            basename_file=$(basename "$file")
            sed -e 's/genesis_core/exordos_core/g' \
                -e 's|/var/lib/genesis|/var/lib/exordos|g' \
                -e 's|/etc/genesis_core|/etc/exordos_core|g' \
                -e 's|/etc/genesis_universal_agent|/etc/exordos_universal_agent|g' \
                "$file" > "$NEW_ETC_DIR/$basename_file"
        fi
    done
    log_info "Configuration migrated to $NEW_ETC_DIR"
fi

# Step 5: Migrate agent configuration
log_info "Step 5: Migrating agent configuration..."
mkdir -p "$NEW_AGENT_ETC_DIR"
if [[ -d "$OLD_AGENT_ETC_DIR" ]]; then
    for file in "$OLD_AGENT_ETC_DIR"/*; do
        if [[ -f "$file" ]]; then
            basename_file=$(basename "$file")
            sed -e 's/genesis_core/exordos_core/g' \
                -e 's|/var/lib/genesis|/var/lib/exordos|g' \
                -e 's|/etc/genesis_core|/etc/exordos_core|g' \
                -e 's|/etc/genesis_universal_agent|/etc/exordos_universal_agent|g' \
                "$file" > "$NEW_AGENT_ETC_DIR/$basename_file"
        fi
    done
    log_info "Agent configuration migrated to $NEW_AGENT_ETC_DIR"
fi

# Step 5.1: Update agent configuration with required drivers and sections
log_info "Step 5.1: Updating agent configuration..."
AGENT_CONF="${NEW_AGENT_ETC_DIR}/genesis_universal_agent.conf"
if [[ -f "$AGENT_CONF" ]]; then
    # Update caps_drivers in [universal_agent]
    python3 - "$AGENT_CONF" "$ADMIN_PASSWORD" << 'PYEOF'
import configparser
import sys

conf_path = sys.argv[1]
admin_password = sys.argv[2]

config = configparser.ConfigParser()
config.read(conf_path)

if "universal_agent" not in config:
    config["universal_agent"] = {}

config["universal_agent"]["caps_drivers"] = (
    "\n"
    "UserCapabilityDriver,\n"
    "PasswordCapabilityDriver,\n"
    "CoreDNSCertificateCapabilityDriver,\n"
    "LBAgentCapabilityDriver,\n"
    "GuestMachineCapabilityDriver,\n"
    "SSHKeyCapabilityDriver"
)

if "universal_agent_scheduler" not in config:
    config["universal_agent_scheduler"] = {}

config["universal_agent_scheduler"]["capabilities"] = (
    "\n"
    "em_*,\n"
    "password,\n"
    "certificate,\n"
    "paas_lb_agent"
)

if "CoreDNSCertificateCapabilityDriver" not in config:
    config["CoreDNSCertificateCapabilityDriver"] = {}
config["CoreDNSCertificateCapabilityDriver"]["username"] = "admin"
config["CoreDNSCertificateCapabilityDriver"]["password"] = admin_password
config["CoreDNSCertificateCapabilityDriver"]["user_api_base_url"] = "http://localhost:11010/v1"

if "UserCapabilityDriver" not in config:
    config["UserCapabilityDriver"] = {}
config["UserCapabilityDriver"]["username"] = "admin"
config["UserCapabilityDriver"]["password"] = admin_password
config["UserCapabilityDriver"]["user_api_base_url"] = "http://localhost:11010"

with open(conf_path, "w") as f:
    config.write(f)

print(f"Agent configuration updated: {conf_path}")
PYEOF
    log_info "Agent configuration updated"
else
    log_warn "Agent config not found: $AGENT_CONF"
fi

# Step 5.2: Update core agent configuration
log_info "Step 5.2: Updating core agent configuration..."
CORE_AGENT_CONF="${NEW_ETC_DIR}/core_agent.conf"
if [[ -f "$CORE_AGENT_CONF" ]]; then
    python3 - "$CORE_AGENT_CONF" << 'PYEOF'
import configparser
import sys

conf_path = sys.argv[1]

config = configparser.ConfigParser()
config.read(conf_path)

# [agent]
if "agent" not in config:
    config["agent"] = {}
config["agent"]["uuid5_name"] = "core_agent"
config["agent"]["target_fields_path"] = "/var/lib/exordos/exordos_core/core_agent/target_fields.json"

# [models]
if "models" not in config:
    config["models"] = {}
config["models"] = {
    "em_core_em_services": "exordos_core.elements.dm.models:Service",
    "em_core_compute_nodes": "exordos_core.compute.dm.models:Node",
    "em_core_compute_sets": "exordos_core.compute.dm.models:NodeSet",
    "em_core_config_configs": "exordos_core.config.dm.models:Config",
    "em_core_network_lb": "exordos_core.user_api.network.dm.models:LB",
    "em_core_network_lb_vhosts": "exordos_core.user_api.network.dm.models:Vhost",
    "em_core_network_lb_vhosts_routes": "exordos_core.user_api.network.dm.models:Route",
    "em_core_network_lb_backend_pools": "exordos_core.user_api.network.dm.models:BackendPool",
    "em_core_secret_passwords": "exordos_core.secret.dm.models:Password",
    "em_core_secret_certificates": "exordos_core.secret.dm.models:Certificate",
    "em_core_dns_domains": "exordos_core.user_api.dns.dm.models:Domain",
    "em_core_dns_domains_records": "exordos_core.user_api.dns.dm.models:Record",
    "em_core_iam_organizations": "exordos_core.user_api.iam.dm.models:Organization",
    "em_core_iam_roles": "exordos_core.user_api.iam.dm.models:Role",
    "em_core_iam_rolebinding": "exordos_core.user_api.iam.dm.models:RoleBinding",
    "em_core_iam_projects": "exordos_core.user_api.iam.dm.models:Project",
    "em_core_iam_permissions": "exordos_core.user_api.iam.dm.models:Permission",
    "em_core_iam_permissionbinding": "exordos_core.user_api.iam.dm.models:PermissionBinding",
    "em_core_vs_profiles": "exordos_core.vs.dm.models:Profile",
    "em_core_vs_variables": "exordos_core.vs.dm.models:Variable",
    "em_core_vs_values": "exordos_core.vs.dm.models:Value",
}

# [filters]
EM_PROJECT = "12345678-c625-4fee-81d5-f691897b8142"
if "filters" not in config:
    config["filters"] = {}
config["filters"] = {
    "em_core_em_services": f"project_id:{EM_PROJECT}",
    "em_core_compute_nodes": f"project_id:{EM_PROJECT}",
    "em_core_compute_sets": f"project_id:{EM_PROJECT}",
    "em_core_config_configs": f"project_id:{EM_PROJECT}",
    "em_core_network_lb": f"project_id:{EM_PROJECT}",
    "em_core_network_lb_vhosts": f"project_id:{EM_PROJECT}",
    "em_core_network_lb_vhosts_routes": f"project_id:{EM_PROJECT}",
    "em_core_network_lb_backend_pools": f"project_id:{EM_PROJECT}",
    "em_core_secret_passwords": f"project_id:{EM_PROJECT}",
    "em_core_secret_certificates": f"project_id:{EM_PROJECT}",
    "em_core_dns_domains": f"project_id:{EM_PROJECT}",
    "em_core_dns_domains_records": f"project_id:{EM_PROJECT}",
    "em_core_vs_profiles": f"project_id:{EM_PROJECT}",
    "em_core_vs_variables": f"project_id:{EM_PROJECT}",
    "em_core_vs_values": f"project_id:{EM_PROJECT}",
}

# [resource_transformer:em_core_vs_variables]
section = "resource_transformer:em_core_vs_variables"
if section not in config:
    config[section] = {}
config[section]["ignore_null_attributes"] = "True"
config[section]["attributes"] = "value"

# [resource_transformer:em_core_secret_passwords]
section = "resource_transformer:em_core_secret_passwords"
if section not in config:
    config[section] = {}
config[section]["ignore_null_attributes"] = "True"
config[section]["attributes"] = "value"

with open(conf_path, "w") as f:
    config.write(f)

print(f"Core agent configuration updated: {conf_path}")
PYEOF
    log_info "Core agent configuration updated"
else
    log_warn "Core agent config not found: $CORE_AGENT_CONF"
fi

sudo mkdir -p /etc/nginx/exordos/

# Step 6: Clone Exordos Core repository
log_info "Step 6: Cloning Exordos Core repository..."

EXORDOS_REPO="https://github.com/exordos/exordos_core.git"

if [[ -d "$NEW_CODE_DIR/.git" ]]; then
    log_warn "Repository already exists at $NEW_CODE_DIR"
    log_info "Pulling latest changes..."
    cd "$NEW_CODE_DIR"
    git pull origin main || true
else
    log_info "Cloning repository from $EXORDOS_REPO..."
    mkdir -p "$NEW_CODE_DIR"
    git clone -b "feat/migraton-rc0" "$EXORDOS_REPO" "$NEW_CODE_DIR"
    log_info "Repository cloned to $NEW_CODE_DIR"
fi

# Step 7: Install dependencies with uv
log_info "Step 7: Installing dependencies with uv..."

# Ensure uv is installed and PATH is set
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv &> /dev/null; then
    log_info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi

cd "$NEW_CODE_DIR"

# Create virtual environment and sync dependencies
log_info "Running uv sync..."
uv sync

log_info "Dependencies installed"

VENV_PATH="$NEW_CODE_DIR/.venv"

# Step 8: Create symlinks for executables
log_info "Step 8: Creating symlinks for executables..."

# Remove old symlinks (nullglob to avoid errors when no matches)
shopt -s nullglob
for link in /usr/local/bin/gc-* /usr/local/bin/genesis-* /usr/bin/gc-* /usr/bin/genesis-*; do
    [[ -L "$link" ]] && rm -f "$link"
done
shopt -u nullglob

# Create new symlinks
sudo ln -sf "$VENV_PATH/bin/ec-user-api" "/usr/bin/ec-user-api"
sudo ln -sf "$VENV_PATH/bin/ec-boot-api" "/usr/bin/ec-boot-api"
sudo ln -sf "$VENV_PATH/bin/ec-orch-api" "/usr/bin/ec-orch-api"
sudo ln -sf "$VENV_PATH/bin/ec-status-api" "/usr/bin/ec-status-api"
sudo ln -sf "$VENV_PATH/bin/ec-gservice" "/usr/bin/ec-gservice"
sudo ln -sf "$VENV_PATH/bin/ec-core-agent" "/usr/bin/ec-core-agent"
sudo ln -sf "$VENV_PATH/bin/ec-bootstrap" "/usr/bin/ec-bootstrap"
sudo ln -sf "$VENV_PATH/bin/ec-bootstrap-templates" "/usr/bin/ec-bootstrap-templates"
sudo ln -sf "$VENV_PATH/bin/exordos-universal-agent" "/usr/bin/exordos-universal-agent"
sudo ln -sf "$VENV_PATH/bin/exordos-universal-agent-db-back" "/usr/bin/exordos-universal-agent-db-back"
sudo ln -sf "$VENV_PATH/bin/exordos-universal-scheduler" "/usr/bin/exordos-universal-scheduler"

log_info "Symlinks created"

# Step 9: Rename genesis_* files and directories to exordos_*
log_info "Step 9: Renaming genesis_* config files to exordos_*..."

for dir in "$NEW_ETC_DIR" "$NEW_AGENT_ETC_DIR"; do
    if [[ -d "$dir" ]]; then
        shopt -s nullglob
        for f in "$dir"/genesis_*; do
            newname="${f/genesis_/exordos_}"
            mv "$f" "$newname"
            log_info "  Renamed: $(basename "$f") -> $(basename "$newname")"
        done
        shopt -u nullglob
    fi
done

# Step 10: Re-install systemd services with new code
log_info "Step 10: Installing systemd service files..."

SYSTEMD_SERVICE_DIR="/etc/systemd/system"
sudo cp "$NEW_CODE_DIR/etc/systemd/ec-user-api.service" $SYSTEMD_SERVICE_DIR/
sudo cp "$NEW_CODE_DIR/etc/systemd/ec-boot-api.service" $SYSTEMD_SERVICE_DIR/
sudo cp "$NEW_CODE_DIR/etc/systemd/ec-orch-api.service" $SYSTEMD_SERVICE_DIR/
sudo cp "$NEW_CODE_DIR/etc/systemd/ec-status-api.service" $SYSTEMD_SERVICE_DIR/
sudo cp "$NEW_CODE_DIR/etc/systemd/ec-gservice.service" $SYSTEMD_SERVICE_DIR/
sudo cp "$NEW_CODE_DIR/etc/systemd/ec-core-agent.service" $SYSTEMD_SERVICE_DIR/
sudo cp "$NEW_CODE_DIR/etc/systemd/exordos-universal-agent.service" $SYSTEMD_SERVICE_DIR/
sudo cp "$NEW_CODE_DIR/etc/systemd/exordos-universal-scheduler.service" $SYSTEMD_SERVICE_DIR/

systemctl daemon-reload
log_info "Systemd services installed"

# Step 11: Run database migrations
log_info "Step 11: Running database migrations..."

source "$VENV_PATH/bin/activate"

# First apply SDK migrations
SDK_MIGRATIONS_PATH="$VENV_PATH/lib/python3.12/site-packages/gcl_sdk/migrations"
log_info "Applying SDK migrations from $SDK_MIGRATIONS_PATH..."
ra-apply-migration --config-dir "$OLD_ETC_DIR/" --path "$SDK_MIGRATIONS_PATH" || true

# Then apply main migrations
log_info "Applying main migrations..."
ra-apply-migration --config-dir "$OLD_ETC_DIR/" --path "$NEW_CODE_DIR/migrations" || true

deactivate

log_info "Migrations applied"

# Step 11.1: Ensure proxy_protocol_from column exists (workaround for 0049 migration)
log_info "Step 11.1: Ensuring proxy_protocol_from column exists..."
sudo -u postgres psql -d "$OLD_DB_NAME" -c "ALTER TABLE \"net_lb_vhosts\" ADD COLUMN IF NOT EXISTS \"proxy_protocol_from\" VARCHAR(18);" 2>/dev/null || true
log_info "proxy_protocol_from column ensured"

# Step 12: Backup database after migrations
log_info "Step 12: Creating database backup..."
BACKUP_DIR="/var/backups"
mkdir -p "$BACKUP_DIR"
sudo -u postgres pg_dump -Fc "$OLD_DB_NAME" > "$BACKUP_DIR/${OLD_DB_NAME}_pre_migration.dump"
log_info "Database backup created at $BACKUP_DIR/${OLD_DB_NAME}_pre_migration.dump"

# Step 12.1: Migrate netplan configuration to use MAC-based matching
log_info "Step 12.1: Migrating netplan configuration..."
NETPLAN_FILE=$(grep -rl 'ethernets:' /etc/netplan/90-*.yaml 2>/dev/null | head -1)
if [[ -n "$NETPLAN_FILE" ]]; then
    log_info "Found netplan config: $NETPLAN_FILE"
    NETPLAN_NEW_FILE="/etc/netplan/90-exordos-net-base-config.yaml"
    sudo python3 -c "
import sys, yaml
mac_main = sys.argv[1]
mac_boot = sys.argv[2]
src = sys.argv[3]
dst = sys.argv[4]
with open(src) as f:
    config = yaml.safe_load(f)
ethernets = config.get('network', {}).get('ethernets', {})
ifaces = list(ethernets.items())
if len(ifaces) < 1:
    print('ERROR: No ethernet interfaces found in netplan config')
    sys.exit(1)
new_ethernets = {}
mac_map = [mac_main, mac_boot]
new_names = ['if-eth0', 'if-eth1']
for idx, (name, cfg) in enumerate(ifaces):
    if idx >= len(mac_map):
        break
    new_cfg = dict(cfg)
    new_cfg['match'] = {'macaddress': mac_map[idx]}
    new_ethernets[new_names[idx]] = new_cfg
config['network']['ethernets'] = new_ethernets
with open(dst, 'w') as f:
    yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
print('Netplan configuration updated: ' + dst)
" "$PORT_MAIN_MAC" "$PORT_BOOT_MAC" "$NETPLAN_FILE" "$NETPLAN_NEW_FILE"
    if [[ "$NETPLAN_FILE" != "$NETPLAN_NEW_FILE" ]]; then
        sudo rm -f "$NETPLAN_FILE"
    fi
    sudo netplan generate || log_warn "netplan generate returned non-zero"
    log_info "Netplan configuration migrated"
else
    log_warn "No netplan configuration file found"
fi

# Step 13: Setup Grub for persistent boot
log_info "Step 13: Configuring Grub..."
cat <<'EOF' | sudo tee /etc/grub.d/40_custom
#!/bin/sh
exec tail -n +3 $0
# This file provides an easy way to add custom menu entries.  Simply type the
# menu entries you want to add after this comment.  Be careful not to change
# the 'exec tail' line above.

menuentry "Autonomous update mode" {
    search --no-floppy --set=root --file /srv/tftp/bios/vmlinuz

    linux /srv/tftp/bios/vmlinuz showopts ip=none net.ifnames=0 biosdevname=0 autonomous=1 console=ttyS0,115200

    initrd /srv/tftp/bios/initrd.img
}
EOF
sudo sed -i 's/^GRUB_TIMEOUT=.*/GRUB_TIMEOUT=3/' /etc/default/grub.d/50-cloudimg-settings.cfg
sudo sed -i '/^GRUB_TIMEOUT_STYLE=/d' /etc/default/grub.d/50-cloudimg-settings.cfg
echo "GRUB_TIMEOUT_STYLE=menu" | sudo tee -a /etc/default/grub.d/50-cloudimg-settings.cfg
echo "GRUB_DEFAULT=3" | sudo tee -a /etc/default/grub.d/50-cloudimg-settings.cfg
sudo update-grub

# Step 14: Download Seed OS image
log_info "Step 14: Downloading Seed OS image..."
sudo wget https://repo.exordos.com/seed_os/2.0.0/initrd.img
sudo wget https://repo.exordos.com/seed_os/2.0.0/vmlinuz
sudo mv initrd.img /srv/tftp/bios/initrd.img
sudo mv vmlinuz /srv/tftp/bios/vmlinuz

# ================================================
# Final migration phase - PostgreSQL and Core Set
# ================================================

SPEC_FILE="/tmp/core_set_spec_$(date +%Y%m%d_%H%M%S).json"
log_info "Generating spec file: $SPEC_FILE"

sed -e "s|{{MACHINE_PREFIX}}|$MACHINE_PREFIX|g" \
    -e "s|{{NETWORK}}|$NETWORK|g" \
    -e "s|{{NODE_UUID}}|$NODE_UUID|g" \
    -e "s|{{CORES}}|$CORES|g" \
    -e "s|{{RAM}}|$RAM|g" \
    -e "s|{{DISK_ROOT_SIZE}}|$DISK_ROOT_SIZE|g" \
    -e "s|{{DISK_DATA_SIZE}}|$DISK_DATA_SIZE|g" \
    -e "s|{{PORT_MAIN_MAC}}|$PORT_MAIN_MAC|g" \
    -e "s|{{PORT_MAIN_IP}}|$PORT_MAIN_IP|g" \
    -e "s|{{PORT_BOOT_MAC}}|$PORT_BOOT_MAC|g" \
    -e "s|{{NETWORK_CIDR}}|$NETWORK_CIDR|g" \
    -e "s|{{BOOT_NETWORK_CIDR}}|$BOOT_NETWORK_CIDR|g" \
    -e "s|{{IMAGE_PATH}}|$IMAGE_PATH|g" \
    -e "s|{{IMAGE_URI}}|$IMAGE_URI|g" \
    -e "s|{{ADMIN_PASSWORD}}|$ADMIN_PASSWORD|g" \
    -e "s|{{IAM_DEFAULT_CLIENT_ID}}|$IAM_DEFAULT_CLIENT_ID|g" \
    -e "s|{{IAM_DEFAULT_CLIENT_SECRET}}|$IAM_DEFAULT_CLIENT_SECRET|g" \
    -e "s|{{IAM_DEFAULT_CLIENT_UUID}}|$IAM_DEFAULT_CLIENT_UUID|g" \
    "$TEMPLATE_FILE" > "$SPEC_FILE"

log_info "Spec file generated successfully"

log_info "================================================"
log_info "Final migration phase - PostgreSQL and Core Set"
log_info "================================================"

# Step 15: Verify PostgreSQL 18 is running (lightweight version assumes PG18 is already installed)
log_info "Step 15: Verifying PostgreSQL ${PG_VERSION} is running..."

if ! systemctl is-active "postgresql@${PG_VERSION}-main" &>/dev/null; then
    log_error "PostgreSQL ${PG_VERSION} is not running. Please ensure PG18 is properly installed and running."
    exit 1
fi

log_info "PostgreSQL ${PG_VERSION} is running"

# Step 16: Verify database exists and create if needed (lightweight version)
log_info "Step 16: Verifying database ${DB_NAME} exists..."

if ! sudo -u postgres psql -d "$DB_NAME" -c "SELECT 1" &>/dev/null; then
    log_info "Database ${DB_NAME} does not exist, creating it..."
    
    # Generate a new secure DB password
    DB_PASSWORD=$(openssl rand -base64 12 | tr -dc 'A-Za-z0-9' | head -c 16)
    log_info "Generated new DB password for ${DB_USER}"

    sudo -u postgres psql -c "DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='${DB_USER}') THEN CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASSWORD}'; ELSE ALTER ROLE ${DB_USER} PASSWORD '${DB_PASSWORD}'; END IF; END \$\$;" 2>/dev/null || true
    sudo -u postgres psql -c "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};" 2>/dev/null || true
    
    log_info "Database ${DB_NAME} created successfully"
else
    log_info "Database ${DB_NAME} already exists"
    
    # Get existing password or generate new one
    DB_PASSWORD=$(openssl rand -base64 12 | tr -dc 'A-Za-z0-9' | head -c 16)
    sudo -u postgres psql -c "ALTER ROLE ${DB_USER} PASSWORD '${DB_PASSWORD}';" 2>/dev/null || true
    log_info "Reset password for ${DB_USER}"
fi

# Step 17: Rename database and update credentials
log_info "Step 17: Renaming database and updating credentials..."

# Rename database from genesis_core to exordos_core
if sudo -u postgres psql -d "genesis_core" -c "SELECT 1" &>/dev/null; then
    log_info "Found database genesis_core, preparing to rename..."
    
    # Disallow new connections and terminate all sessions
    log_info "Disallowing new connections to genesis_core..."
    sudo -u postgres psql -c "ALTER DATABASE genesis_core WITH ALLOW_CONNECTIONS false;" || true
    
    log_info "Terminating all sessions to genesis_core..."
    sudo -u postgres psql -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'genesis_core' AND pid <> pg_backend_pid();" || true
    sleep 2
    
    # Terminate again in case new sessions connected
    sudo -u postgres psql -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'genesis_core' AND pid <> pg_backend_pid();" || true
    sleep 1
    
    # If exordos_core exists (likely empty, created in Step 16), drop it first
    if sudo -u postgres psql -d "exordos_core" -c "SELECT 1" &>/dev/null; then
        log_info "Database exordos_core exists, dropping it before rename..."
        sudo -u postgres psql -c "DROP DATABASE exordos_core;" || {
            log_warn "Failed to drop exordos_core, attempting rename anyway"
        }
    fi
    
    log_info "Renaming database genesis_core to exordos_core..."
    sudo -u postgres psql -c "ALTER DATABASE genesis_core RENAME TO exordos_core;" || {
        log_error "Failed to rename database"
        exit 1
    }
    log_info "Database renamed successfully"
    
    # Allow connections to renamed database
    sudo -u postgres psql -c "ALTER DATABASE exordos_core WITH ALLOW_CONNECTIONS true;" || true
    
    # Drop genesis_core user if exists
    log_info "Dropping genesis_core user..."
    sudo -u postgres psql -c "DROP USER IF EXISTS genesis_core;" || log_warn "Failed to drop genesis_core user"
    
    # Grant permissions to exordos_core user on exordos_core database
    log_info "Granting permissions to exordos_core user..."
    sudo -u postgres psql -d "exordos_core" -c "GRANT ALL PRIVILEGES ON SCHEMA public TO exordos_core;" || true
    sudo -u postgres psql -d "exordos_core" -c "GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO exordos_core;" || true
    sudo -u postgres psql -d "exordos_core" -c "GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO exordos_core;" || true
    sudo -u postgres psql -d "exordos_core" -c "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO exordos_core;" || true
    sudo -u postgres psql -d "exordos_core" -c "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO exordos_core;" || true
    log_info "Permissions granted to exordos_core user"
else
    log_info "Database genesis_core not found or already renamed"
fi

# Update DB credentials in config files
NEW_CONN_URL="postgresql://${DB_USER}:${DB_PASSWORD}@127.0.0.1:5432/${DB_NAME}"

for conf in \
    "${NEW_ETC_DIR}/exordos_core.conf" \
    "${NEW_ETC_DIR}/core_agent.conf" \
    "${NEW_AGENT_ETC_DIR}/exordos_universal_agent.conf" \
    "${NEW_AGENT_ETC_DIR}/genesis_universal_agent.conf"
do
    if [[ -f "$conf" ]]; then
        sed -i -E "s|connection_url = postgresql://[^@]+@[^/]+/[^ ]+|connection_url = ${NEW_CONN_URL}|g" "$conf"
        log_info "  Updated connection_url in $conf"
    fi
done

for pdns_conf in /etc/powerdns/pdns.d/genesis.conf /etc/powerdns/pdns.d/exordos.conf; do
    if [[ -f "$pdns_conf" ]]; then
        sed -i -E "s|^gpgsql-dbname=.*|gpgsql-dbname=${DB_NAME}|" "$pdns_conf"
        sed -i -E "s|^gpgsql-user=.*|gpgsql-user=${DB_USER}|" "$pdns_conf"
        sed -i -E "s|^gpgsql-password=.*|gpgsql-password=${DB_PASSWORD}|" "$pdns_conf"
        log_info "  Updated gpgsql credentials in $pdns_conf"
    fi
done
log_info "DB credentials updated in all config files"

if sudo -u postgres psql -d "$DB_NAME" -c "SELECT 1" &>/dev/null; then
    log_info "Database ${DB_NAME} is accessible"
else
    log_error "Cannot access database ${DB_NAME}"
    exit 1
fi

# Step 18: Prepare PostgreSQL data on genesis disk before remount
log_info "Step 18: Preparing PostgreSQL data on genesis disk before remount..."

PG_CONF_FILE="/etc/postgresql/${PG_VERSION}/main/postgresql.conf"
GENESIS_PGDATA="/var/lib/genesis/data/postgresql/${PG_VERSION}/main"
NEW_PGDATA="/var/lib/genesis/data/var/lib/postgresql/${PG_VERSION}/main"

log_info "PostgreSQL current data dir: ${GENESIS_PGDATA}"
log_info "PostgreSQL target data dir: ${NEW_PGDATA}"

# Step 1: Stop PostgreSQL
systemctl stop postgresql 2>/dev/null || true

# Wait for PostgreSQL to fully shut down
log_info "Waiting for PostgreSQL to fully shut down..."
for i in $(seq 1 10); do
    if ! systemctl is-active "postgresql@${PG_VERSION}-main" &>/dev/null; then
        log_info "PostgreSQL is stopped"
        break
    fi
    if [[ $i -eq 10 ]]; then
        log_warn "PostgreSQL still active after 10 seconds, proceeding anyway"
    fi
    sleep 1
done

# Step 2: Move PostgreSQL data to correct structure on genesis disk
if [[ -d "$GENESIS_PGDATA" ]]; then
    log_info "Moving PostgreSQL data from $GENESIS_PGDATA to $NEW_PGDATA"
    mkdir -p "$NEW_PGDATA"
    mv "$GENESIS_PGDATA"/* "$NEW_PGDATA/"
    chown -R postgres:postgres "$NEW_PGDATA"
    
    if [[ -f "${NEW_PGDATA}/PG_VERSION" ]]; then
        log_info "PostgreSQL data successfully moved"
    else
        log_error "Failed to move PostgreSQL data"
        exit 1
    fi
else
    log_error "PostgreSQL data not found at $GENESIS_PGDATA"
    exit 1
fi

# Step 3: Update postgresql.conf to use new data directory (will be /persist after remount)
PERSISTENT_PGDATA="${PERSISTENT_MOUNT}/var/lib/postgresql/${PG_VERSION}/main"
if grep -qs "^[[:space:]]*data_directory[[:space:]]*=" "${PG_CONF_FILE}"; then
    sed -i "s|^[[:space:]]*data_directory[[:space:]]*=.*|data_directory = '${PERSISTENT_PGDATA}'|" "${PG_CONF_FILE}"
else
    printf '%s\n' "data_directory = '${PERSISTENT_PGDATA}'" >> "${PG_CONF_FILE}"
fi

if grep -qs "^[[:space:]]*port[[:space:]]*=" "${PG_CONF_FILE}"; then
    sed -i "s|^[[:space:]]*port[[:space:]]*=.*|port = 5432|" "${PG_CONF_FILE}"
else
    printf '%s\n' "port = 5432" >> "${PG_CONF_FILE}"
fi

log_info "postgresql.conf updated to use persistent storage path"

# Step 19: Remount disk to /persist/ and prepare for migration
log_info "Step 19: Remounting disk to /persist/ and preparing for migration..."

# Check current mount situation
GENESIS_DATA_MOUNT="/var/lib/genesis/data"
DATA_DISK="/dev/vdb1"

if mountpoint -q "$GENESIS_DATA_MOUNT"; then
    log_info "Found disk mounted at ${GENESIS_DATA_MOUNT}, will remount to ${PERSISTENT_MOUNT}"
    
    # Create /persist/ directory
    mkdir -p "$PERSISTENT_MOUNT"
    
    # Check for processes using the mount point
    if lsof +D "$GENESIS_DATA_MOUNT" >/dev/null 2>&1; then
        log_warn "Processes are using ${GENESIS_DATA_MOUNT}, attempting to stop them..."
        # Try to stop PostgreSQL service if it's using the mount
        systemctl stop postgresql 2>/dev/null || true
        sleep 2
        
        # Check again
        if lsof +D "$GENESIS_DATA_MOUNT" >/dev/null 2>&1; then
            log_warn "Still processes using ${GENESIS_DATA_MOUNT}, showing them:"
            lsof +D "$GENESIS_DATA_MOUNT" || true
            log_warn "Attempting force unmount..."
        fi
    fi
    
    # Unmount from genesis location with force option if needed
    if ! umount "$GENESIS_DATA_MOUNT" 2>/dev/null; then
        log_warn "Normal unmount failed, trying lazy unmount..."
        umount -l "$GENESIS_DATA_MOUNT" || {
            log_error "Failed to unmount ${GENESIS_DATA_MOUNT} even with lazy option"
            exit 1
        }
    fi
    
    # Mount to /persist/
    mount "$DATA_DISK" "$PERSISTENT_MOUNT" || {
        log_error "Failed to mount ${DATA_DISK} to ${PERSISTENT_MOUNT}"
        exit 1
    }
    
    log_info "Successfully remounted ${DATA_DISK} from ${GENESIS_DATA_MOUNT} to ${PERSISTENT_MOUNT}"
elif mountpoint -q "$PERSISTENT_MOUNT"; then
    log_warn "${PERSISTENT_MOUNT} is already mounted"
else
    # Create /persist/ directory and mount
    mkdir -p "$PERSISTENT_MOUNT"
    mount "$DATA_DISK" "$PERSISTENT_MOUNT" || {
        log_error "Failed to mount ${DATA_DISK} to ${PERSISTENT_MOUNT}"
        exit 1
    }
    log_info "Mounted ${DATA_DISK} to ${PERSISTENT_MOUNT}"
fi

# Step 20: Start PostgreSQL after disk remount and verify it's working
log_info "Step 20: Starting PostgreSQL after disk remount..."

# Set correct ownership and permissions for PostgreSQL data directory
sudo chown -R postgres:postgres "${PERSISTENT_MOUNT}/var/lib/postgresql"
sudo chmod 700 "${PERSISTENT_MOUNT}/var/lib/postgresql/${PG_VERSION}/main"

# Reload systemd and start PostgreSQL
systemctl daemon-reload || true
log_info "Starting PostgreSQL service..."

if ! systemctl start "postgresql@${PG_VERSION}-main" 2>&1; then
    log_error "PostgreSQL service failed to start!"
    systemctl status "postgresql@${PG_VERSION}-main" --no-pager || true
    journalctl -u "postgresql@${PG_VERSION}-main" -n 50 --no-pager || true
    exit 1
fi

sleep 3

# Verify PostgreSQL is running
if ! systemctl is-active "postgresql@${PG_VERSION}-main" &>/dev/null; then
    log_error "PostgreSQL service is not active after start attempt"
    systemctl status "postgresql@${PG_VERSION}-main" --no-pager || true
    exit 1
fi

log_info "PostgreSQL service is active"

# Verify database is accessible
if sudo -u postgres psql -d "$DB_NAME" -c "SELECT 1" &>/dev/null; then
    log_info "PostgreSQL is working with persistent storage"
else
    log_error "PostgreSQL is not accessible after migration"
    exit 1
fi

# Step 21: Migrate other data to persistent storage
log_info "Step 21: Migrating other data to persistent storage..."
mkdir -p "${PERSISTENT_MOUNT}/var/lib/exordos/data"
mkdir -p "${PERSISTENT_MOUNT}/var/lib/exordos/exordos_core"

# Step 22: Apply scheduler migration hack patch
log_info "Step 22: Applying scheduler migration hack patch..."
PATCH_SOURCE="${SCHEDULER_PATH}"
PATCH_DEST="${NEW_CODE_DIR}/scheduler_migration_hack.patch"

if [[ -f "$PATCH_SOURCE" ]]; then
    cp "$PATCH_SOURCE" "$PATCH_DEST"
    log_info "Patch copied to ${PATCH_DEST}"

    # Replace placeholder UUID with actual pool UUID
    sed -i "s/00000011-0000-0001-0000-000000000011/${POOL_UUID}/g" "$PATCH_DEST"
    log_info "Patch updated with pool UUID: ${POOL_UUID}"

    # Apply the patch
    cd "$NEW_CODE_DIR"
    if git apply "$PATCH_DEST"; then
        log_info "Patch applied successfully"
    else
        log_warn "Failed to apply patch (may already be applied or not needed)"
    fi
    # rm -f "$PATCH_DEST"
else
    log_warn "Scheduler patch file not found: ${PATCH_SOURCE}"
fi

# Step 23: Install Exordos CLI
log_info "Step 23: Install Exordos CLI..."

# Remove old exordos binary and configuration
log_info "Removing old exordos installation..."
rm -f /usr/local/bin/exordos
rm -f /root/.local/bin/exordos
rm -rf /root/.exordos
rm -rf ~/.exordos
log_info "Old exordos installation removed"

# Install fresh exordos CLI with PATH fix
curl -fsSL https://repo.exordos.com/install.sh | sudo PATH="/root/.local/bin:$PATH" sh

# Update PATH and verify installation
export PATH="$PATH:/root/.local/bin"
if [[ -f "/root/.local/bin/exordos" ]]; then
    log_info "Exordos CLI installed successfully at /root/.local/bin/exordos"
    # Test the binary
    /root/.local/bin/exordos --version || log_warn "Exordos binary test failed"
else
    log_error "Exordos CLI installation failed - binary not found"
    exit 1
fi

# Step 24: Enable exordos core services
log_info "Step 24: Enable exordos core services..."
sudo systemctl enable --now \
    ec-user-api \
    ec-orch-api \
    ec-status-api \
    ec-boot-api \
    ec-gservice \
    ec-core-agent \
    exordos-universal-agent \
    exordos-universal-scheduler \

# Wait for ec-user-api to become available
log_info "Waiting for ec-user-api to be ready on port 11010..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:11010/ > /dev/null 2>&1; then
        log_info "ec-user-api is ready"
        break
    fi
    if [[ $i -eq 30 ]]; then
        log_warn "ec-user-api did not become ready in 30s, proceeding anyway"
    fi
    sleep 1
done

# Step 25: Bootstrap core element
log_info "Step 25: Bootstrapping core element..."
log_info "Using spec file: ${SPEC_FILE}"

# Fix NULL project_id in em_elements table
log_info "Fixing NULL project_id in em_elements table..."
sudo -u postgres psql -d "${DB_NAME}" -c "UPDATE em_elements SET project_id = '12345678-0000-0000-0000-000000000000' WHERE project_id IS NULL;" || true
log_info "project_id fixed"

VENV_PATH="${NEW_CODE_DIR}/.venv"
if [[ ! -d "$VENV_PATH" ]]; then
    log_error "Virtual environment not found at ${VENV_PATH}"
    exit 1
fi

PYTHON_HELPER="/tmp/bootstrap_core_element.py"
cat > "$PYTHON_HELPER" << PYTHON_EOF
#!/usr/bin/env python3
"""Helper script to bootstrap core element."""

import json
import sys
import time
import traceback
import uuid as sys_uuid

import yaml

def _install_or_upgrade_element_manifest(element_name, manifest_path, spec):
    """Install or upgrade element manifest via HTTP API."""
    import os

    from gcl_sdk.clients.http import base as http_base
    from oslo_config import cfg
    from restalchemy.dm import filters as dm_filters

    from exordos_core.elements.dm import models as em_models

    CONF = cfg.CONF
    MANIFEST_COLLECTION = "/v1/em/manifests/"

    if not os.path.exists(manifest_path):
        print(f"ERROR: No manifest file found at {manifest_path}")
        return

    with open(manifest_path) as f:
        manifest_data = yaml.safe_load(f)

    auth = http_base.CoreIamAuthenticator(
        base_url="http://localhost:11010",
        username="admin",
        password=spec["admin_password"],
        client_id=spec["iam"]["default_client_id"],
        client_secret=spec["iam"]["default_client_secret"],
        client_uuid=sys_uuid.UUID(spec["iam"]["default_client_uuid"]),
    )

    client = http_base.CollectionBaseClient(
        base_url="http://localhost:11010", auth=auth
    )

    element = em_models.Element.objects.get_one_or_none(
        filters={"name": dm_filters.EQ(element_name)}
    )

    if element is not None:
        print(f"Element '{element_name}' exists, performing upgrade...")
        manifest_data = client.create(MANIFEST_COLLECTION, manifest_data)
        new_uuid = manifest_data["uuid"]

        # Delete old manifests with the same name but different uuid
        # existing_manifests = client.filter(MANIFEST_COLLECTION)
        # for m in existing_manifests:
        #     if m.get("name") == manifest_data.get("name") and m["uuid"] != new_uuid:
        #         print(f"Deleting old manifest {m['uuid']}...")
        #         client.delete(MANIFEST_COLLECTION, m["uuid"])
        # time.sleep(5)

        client.do_action(
            MANIFEST_COLLECTION, "upgrade", new_uuid, invoke=True
        )
        print(f"Element '{element_name}' upgrade initiated")
    else:
        print(f"Element '{element_name}' not found, performing install...")
        manifest_data = client.create(MANIFEST_COLLECTION, manifest_data)
        client.do_action(
            MANIFEST_COLLECTION, "install", manifest_data["uuid"], invoke=True
        )
        print(f"Element '{element_name}' install initiated")


def main(spec_file, config_file):
    with open(spec_file, 'r') as f:
        spec = json.load(f)

    try:
        from oslo_config import cfg
        from restalchemy.common import config_opts as ra_config_opts
        from restalchemy.storage import exceptions as ra_exceptions
        from restalchemy.storage.sql import engines
        from exordos_core.bootstrap import defaults as bootstrap_defaults
        from exordos_core.common import config as exordos_config
        from exordos_core.cmd import bootstrap as bootstrap_cmd
    except ImportError as e:
        print(f"ERROR: Cannot import modules: {e}")
        sys.exit(1)

    CONF = cfg.CONF
    ra_config_opts.register_posgresql_db_opts(CONF)

    try:
        exordos_config.parse(["--config-file", config_file])
    except Exception as e:
        print(f"ERROR: Failed to parse config {config_file}: {e}")
        sys.exit(1)

    try:
        engines.engine_factory.configure_postgresql_factory(CONF)
    except Exception as e:
        print(f"ERROR: Failed to configure DB engine: {e}")
        sys.exit(1)

    try:
        node_set = bootstrap_defaults.add_core_set(spec, set_active=True)
        print(f"SUCCESS: Core set {node_set.uuid} registered")
    except ra_exceptions.ConflictRecords:
        print("INFO: Core set already exists in database")
    except Exception as e:
        print(f"ERROR: Failed to register core set: {e}")
        return 1

    try:
        bootstrap_cmd._ensure_exordos_config(spec)
        _install_or_upgrade_element_manifest("core", "${MANIFEST_FILE}", spec)
        print("Waiting for core manifest to be processed...")
        time.sleep(20)

        bootstrap_defaults.activate_profile("${PROFILE}")
        print("Profile activated")
        
        # Set variables for core element
        bootstrap_defaults.set_core_ip_var("${PORT_MAIN_IP}")
        print("Core IP variable set")
        
        bootstrap_defaults.set_core_root_disk_size_var(spec)
        print("Core root disk size variable set")
        
        bootstrap_defaults.set_core_data_disk_size_var(spec)
        print("Core data disk size variable set")

        bootstrap_defaults.set_iam_default_client_uuid_var(spec)
        print("IAM default client UUID variable set")

        bootstrap_defaults.set_iam_default_client_id_var(spec)
        print("IAM default client ID variable set")

        bootstrap_defaults.set_iam_default_client_secret_var(spec)
        print("IAM default client secret variable set")

        bootstrap_defaults.set_hs256_jwks_encryption_key_var("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
        print("HS256 JWKS encryption key variable set")

    except Exception as e:
        print(f"ERROR: Failed to bootstrap core element: {e}")
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: bootstrap_core_element.py <spec_file> <config_file>")
        sys.exit(1)
    sys.exit(main(sys.argv[1], sys.argv[2]))
PYTHON_EOF

chmod +x "$PYTHON_HELPER"

source "${VENV_PATH}/bin/activate"
python3 "$PYTHON_HELPER" "$SPEC_FILE" "${NEW_ETC_DIR}/exordos_core.conf"
REG_RESULT=$?
rm -f "$PYTHON_HELPER"

if [[ $REG_RESULT -ne 0 ]]; then
    log_error "Core element bootstrap failed"
    exit 1
fi

log_info "Core element bootstrapped successfully"

# Step 26: Revert scheduler migration hack patch
# log_info "Step 26: Reverting scheduler migration hack patch..."
# cd "$NEW_CODE_DIR"
# if git checkout exordos_core/compute/scheduler/service.py; then
#     log_info "Scheduler service.py reverted successfully"
# else
#     log_warn "Failed to revert scheduler service.py (file may not be modified)"
# fi

log_info "Restarting ec-gservice..."
sudo systemctl restart ec-gservice
log_info "ec-gservice restarted successfully"

# Step 27: Copy config files into /var/lib/exordos/data/ (persisted location)
log_info "Step 27: Persisting config files to /var/lib/exordos/data/..."
DATA_DIR="/var/lib/exordos/data"
declare -A PERSIST_FILES=(
    ["/etc/exordos_universal_agent/exordos_universal_agent.conf"]="${DATA_DIR}/etc/exordos_universal_agent/exordos_universal_agent.conf"
    ["/etc/exordos_core/exordos_core.conf"]="${DATA_DIR}/etc/exordos_core/exordos_core.conf"
    ["/etc/exordos_core/core_agent.conf"]="${DATA_DIR}/etc/exordos_core/core_agent.conf"
    ["/etc/powerdns/pdns.d/exordos.conf"]="${DATA_DIR}/etc/powerdns/pdns.d/exordos.conf"
    ["/etc/powerdns/pdns.d/genesis.conf"]="${DATA_DIR}/etc/powerdns/pdns.d/exordos.conf"
    ["/etc/systemd/resolved.conf"]="${DATA_DIR}/etc/systemd/resolved.conf"
    ["/etc/dnsdist/dnsdist-private.conf"]="${DATA_DIR}/etc/dnsdist/dnsdist-private.conf"
    ["/etc/netplan/90-exordos-net-base-config.yaml"]="${DATA_DIR}/etc/netplan/90-exordos-net-base-config.yaml"
    ["/etc/netplan/90-genesis-net-base-config.yaml"]="${DATA_DIR}/etc/netplan/90-exordos-net-base-config.yaml"
)
for src in "${!PERSIST_FILES[@]}"; do
    dst="${PERSIST_FILES[$src]}"
    if [[ -f "$src" ]]; then
        sudo mkdir -p "$(dirname "$dst")"
        sudo cp "$src" "$dst"
        log_info "  Persisted $src -> $dst"
    else
        log_warn "  Source file not found, skipping: $src"
    fi
done
log_info "Config files persisted"


# Step 28: Copy persistent data to /persist/
log_info "Step 28: Copying persistent data to ${PERSISTENT_MOUNT}..."
for src_dir in /root/ /home/ /etc/exordos_core/ /var/lib/exordos/exordos_core/ /var/lib/exordos/data/; do
    if [[ -d "$src_dir" ]]; then
        dest_dir="${PERSISTENT_MOUNT}${src_dir}"
        sudo mkdir -p "$dest_dir"
        sudo cp -a "$src_dir/." "$dest_dir/"
        log_info "  Copied $src_dir -> $dest_dir"
    else
        log_warn "  Source directory not found, skipping: $src_dir"
    fi
done
log_info "Persistent data copied"

log_info "================================================"
log_info "Migration completed!"
log_info "================================================"
log_info "Summary:"
log_info "  - PostgreSQL ${PG_VERSION} is running with persistent storage"
log_info "  - Database backup restored to persistent disk"
log_info "  - Core element bootstrapped"
log_info ""
log_info "Next steps:"
log_info "  1. Verify database: sudo -u postgres psql -d ${DB_NAME} -c 'SELECT version();'"
log_info "  2. Check persistent disk: df -h ${PERSISTENT_MOUNT}"
log_info "  3. Start Exordos services: systemctl start exordos-universal-agent"
log_info "  4. Check service status: systemctl status exordos-universal-agent"
log_info "================================================"
