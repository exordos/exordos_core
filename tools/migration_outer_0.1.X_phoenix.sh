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

# Migration script for phoenix hypervisor (outer)
# Manages VM volumes and libvirt configuration

set -euo pipefail

# Get parameters from command line arguments
PREFIX="${1:-}"
STORAGE_POOL="${2:-}"
ROOT_DISK_SIZE_GB="${3:-}"
DATA_DISK_SIZE_GB="${4:-}"
NETWORK="${5:-}"
CIDR="${6:-}"

if [[ -z "$PREFIX" ]]; then
    echo "ERROR: Prefix parameter is required"
    echo "Usage: $0 <prefix> <storage_pool> <root_disk_size_gb> <data_disk_size_gb> <network> <cidr>"
    echo "Example: $0 phoenix vmpool 20 50 br0 10.20.0.0/22"
    exit 1
fi

if [[ -z "$STORAGE_POOL" ]]; then
    echo "ERROR: Storage pool parameter is required"
    echo "Usage: $0 <prefix> <storage_pool> <root_disk_size_gb> <data_disk_size_gb> <network> <cidr>"
    echo "Example: $0 phoenix vmpool 20 50 br0 10.20.0.0/22"
    exit 1
fi

if [[ -z "$ROOT_DISK_SIZE_GB" ]]; then
    echo "ERROR: Root disk size parameter is required"
    echo "Usage: $0 <prefix> <storage_pool> <root_disk_size_gb> <data_disk_size_gb> <network> <cidr>"
    echo "Example: $0 phoenix vmpool 20 50 br0 10.20.0.0/22"
    exit 1
fi

if [[ -z "$DATA_DISK_SIZE_GB" ]]; then
    echo "ERROR: Data disk size parameter is required"
    echo "Usage: $0 <prefix> <storage_pool> <root_disk_size_gb> <data_disk_size_gb> <network> <cidr>"
    echo "Example: $0 phoenix vmpool 20 50 br0 10.20.0.0/22"
    exit 1
fi

if [[ -z "$NETWORK" ]]; then
    echo "ERROR: Network parameter is required"
    echo "Usage: $0 <prefix> <storage_pool> <root_disk_size_gb> <data_disk_size_gb> <network> <cidr>"
    echo "Example: $0 phoenix vmpool 20 50 br0 10.20.0.0/22"
    exit 1
fi

if [[ -z "$CIDR" ]]; then
    echo "ERROR: CIDR parameter is required"
    echo "Usage: $0 <prefix> <storage_pool> <root_disk_size_gb> <data_disk_size_gb> <network> <cidr>"
    echo "Example: $0 phoenix vmpool 20 50 br0 10.20.0.0/22"
    exit 1
fi

# Configuration
VM_NAME="genesis-core-bootstrap"

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

log_info "Starting hypervisor migration tasks"
log_info "===================================="
log_info "Prefix: $PREFIX"
log_info "Storage pool: $STORAGE_POOL"
log_info "Root disk size: ${ROOT_DISK_SIZE_GB}GB"
log_info "Data disk size: ${DATA_DISK_SIZE_GB}GB"
log_info "Network: $NETWORK"
log_info "CIDR: $CIDR"

# Check VM exists
if ! virsh list --all --name | grep -q "^${VM_NAME}$"; then
    log_error "VM '$VM_NAME' not found"
    log_info "Available VMs:"
    virsh list --all --name | grep -E "(genesis|exordos)" || true
    exit 1
fi

log_info "Found VM: $VM_NAME"

# Step 1: Get current VM configuration
log_info "Step 1: Analyzing current VM configuration..."
VM_XML=$(virsh dumpxml "$VM_NAME" 2>/dev/null)
if [[ -z "$VM_XML" ]]; then
    log_error "Failed to get VM XML"
    exit 1
fi

# Extract VM UUID and construct new VM name
VM_UUID=$(echo "$VM_XML" | grep -oP '(?<=<uuid>)[^<]+' | head -1 || true)
if [[ -z "$VM_UUID" ]]; then
    log_error "Failed to extract VM UUID from XML"
    exit 1
fi

VM_UUID_SHORT="${VM_UUID:0:8}"
NEW_VM_NAME="${PREFIX}-${VM_UUID_SHORT}-exordos-core-bootstrap"

log_info "VM UUID: $VM_UUID"
log_info "VM UUID (first 8 chars): $VM_UUID_SHORT"
log_info "New VM name will be: $NEW_VM_NAME"

# Get storage pool path from virsh
STORAGE_POOL_PATH=$(virsh pool-dumpxml "$STORAGE_POOL" 2>/dev/null | grep -oP '(?<=<path>)[^<]+' | head -1 || true)
if [[ -z "$STORAGE_POOL_PATH" ]]; then
    log_error "Failed to get path for storage pool '$STORAGE_POOL'"
    exit 1
fi
log_info "Storage pool path: $STORAGE_POOL_PATH"

# Extract current volumes (libvirt uses single quotes in XML attributes)
CURRENT_VOLUMES=$(echo "$VM_XML" | grep -oP "(?<=<source file=')[^']+" || true)
log_info "Current volumes:"
echo "$CURRENT_VOLUMES" | while read -r vol; do
    if [[ -n "$vol" ]]; then
        log_info "  - $vol"
        # Get volume info
        virsh vol-info "$vol" 2>/dev/null | grep -E "^Name:|^Capacity:" || true
    fi
done

# Step 2: Calculate volume UUIDs using uuid5
log_info "Step 2: Calculating volume UUIDs..."
# Root volume: uuid5(VM_UUID, "root-volume")
# Data volume: uuid5(VM_UUID, "data")

# Use Python to generate UUIDv5 since bash doesn't have built-in support
ROOT_VOLUME_UUID=$(python3 -c "
import uuid
namespace = uuid.UUID('$VM_UUID')
print(uuid.uuid5(namespace, 'root-volume'))
")

DATA_VOLUME_UUID=$(python3 -c "
import uuid
namespace = uuid.UUID('$VM_UUID')
print(uuid.uuid5(namespace, 'data'))
")

log_info "Root volume UUID: $ROOT_VOLUME_UUID"
log_info "Data volume UUID: $DATA_VOLUME_UUID"

# Get the first volume path (root disk)
ROOT_VOL_PATH=""
while IFS= read -r vol_path; do
    if [[ -n "$vol_path" && -e "$vol_path" ]]; then
        ROOT_VOL_PATH="$vol_path"
        break
    fi
done <<< "$CURRENT_VOLUMES"

if [[ -z "$ROOT_VOL_PATH" ]]; then
    log_error "No root volume found for VM $VM_NAME"
    log_info "Volumes detected in XML:"
    echo "$CURRENT_VOLUMES"
    log_info "Trying virsh domblklist as fallback..."
    ROOT_VOL_PATH=$(virsh domblklist "$VM_NAME" --details 2>/dev/null \
        | awk '$2 == "disk" && $3 == "file" {print $4; exit}' || true)
    if [[ -z "$ROOT_VOL_PATH" ]]; then
        log_error "Could not determine root volume path"
        exit 1
    fi
    log_info "Found via domblklist: $ROOT_VOL_PATH"
fi

ROOT_VOL_NAME=$(basename "$ROOT_VOL_PATH")
NEW_ROOT_VOL_NAME="${ROOT_VOLUME_UUID}.qcow2"
NEW_DATA_VOL_NAME="${DATA_VOLUME_UUID}.qcow2"

# Detect source pool (may differ from target STORAGE_POOL)
SRC_POOL=$(virsh vol-pool "$ROOT_VOL_PATH" 2>/dev/null || true)
# Save original path before it gets overwritten in step 4
ORIG_ROOT_VOL_PATH="$ROOT_VOL_PATH"

log_info "Root volume will be renamed: $ROOT_VOL_NAME -> $NEW_ROOT_VOL_NAME"
log_info "Data volume will be created: $NEW_DATA_VOL_NAME"

# Step 3: Shutdown VM
log_info "Step 3: Shutting down VM..."
VM_STATE=$(virsh domstate "$VM_NAME" 2>/dev/null || echo "unknown")
log_info "Current VM state: $VM_STATE"

if [[ "$VM_STATE" == "running" ]]; then
    log_info "Shutting down VM $VM_NAME..."
    virsh shutdown "$VM_NAME"

    # Wait for VM to stop
    log_info "Waiting for VM to stop..."
    for i in {1..30}; do
        sleep 2
        VM_STATE=$(virsh domstate "$VM_NAME" 2>/dev/null || echo "unknown")
        if [[ "$VM_STATE" != "running" ]]; then
            log_info "VM stopped (state: $VM_STATE)"
            break
        fi
        log_info "  ... still running (attempt $i/30)"
    done

    # Force destroy if still running
    VM_STATE=$(virsh domstate "$VM_NAME" 2>/dev/null || echo "unknown")
    if [[ "$VM_STATE" == "running" ]]; then
        log_warn "VM still running after shutdown, forcing destroy..."
        virsh destroy "$VM_NAME"
        sleep 2
    fi
fi

VM_STATE=$(virsh domstate "$VM_NAME" 2>/dev/null || echo "unknown")
if [[ "$VM_STATE" == "running" ]]; then
    log_error "Failed to stop VM $VM_NAME"
    exit 1
fi

log_info "VM is stopped"

# Step 4: Rename root volume
log_info "Step 4: Renaming root volume..."
log_info "  $ROOT_VOL_NAME -> $NEW_ROOT_VOL_NAME"

NEW_ROOT_VOL_PATH="${STORAGE_POOL_PATH}/${NEW_ROOT_VOL_NAME}"

# Refresh pool to ensure libvirt sees current files
virsh pool-refresh "$STORAGE_POOL" &>/dev/null || true

# Check if new volume already exists (idempotent)
if virsh vol-info "$NEW_ROOT_VOL_NAME" --pool "$STORAGE_POOL" &>/dev/null; then
    log_warn "New root volume already exists: $NEW_ROOT_VOL_NAME, skipping clone"
    ROOT_VOL_PATH="$NEW_ROOT_VOL_PATH"
else
    # Use cp to rename: faster than clone for large volumes
    cp "$ROOT_VOL_PATH" "$NEW_ROOT_VOL_PATH"
    virsh pool-refresh "$STORAGE_POOL"
    log_info "Root volume copied to: $NEW_ROOT_VOL_NAME"
    # Delete old volume only if it still exists (may be in a different source pool)
    if [[ -n "$SRC_POOL" ]] && virsh vol-info "$ROOT_VOL_NAME" --pool "$SRC_POOL" &>/dev/null; then
        virsh vol-delete "$ROOT_VOL_NAME" --pool "$SRC_POOL"
        log_info "Old root volume deleted from pool '$SRC_POOL': $ROOT_VOL_NAME"
    elif [[ -f "$ROOT_VOL_PATH" ]]; then
        rm -f "$ROOT_VOL_PATH"
        log_info "Old root volume file removed: $ROOT_VOL_PATH"
    fi
    ROOT_VOL_PATH="$NEW_ROOT_VOL_PATH"
    log_info "Root volume renamed to: $NEW_ROOT_VOL_NAME"
fi

# Step 5: Create and attach data volume
log_info "Step 5: Creating data volume for PG18..."

NEW_DATA_VOL_PATH="${STORAGE_POOL_PATH}/${NEW_DATA_VOL_NAME}"

log_info "Creating ${DATA_DISK_SIZE_GB}GB volume: $NEW_DATA_VOL_NAME"

# Check if data volume already exists
if virsh vol-info "$NEW_DATA_VOL_NAME" --pool "$STORAGE_POOL" &>/dev/null; then
    log_warn "Data volume already exists: $NEW_DATA_VOL_NAME"
else
    virsh vol-create-as "$STORAGE_POOL" "$NEW_DATA_VOL_NAME" "${DATA_DISK_SIZE_GB}G" \
        --format qcow2 \
        --allocation 0 \
        --prealloc-metadata
    log_info "Data volume created: $NEW_DATA_VOL_NAME"
fi

# Attach data volume as vdb
log_info "Attaching data volume as vdb..."

# Check if disk is already attached (re-read XML after volume operations)
CURRENT_VM_XML=$(virsh dumpxml "$VM_NAME" 2>/dev/null || true)
if echo "$CURRENT_VM_XML" | grep -q "<target dev='vdb'"; then
    log_warn "Disk vdb already attached to VM"
else
    virsh attach-disk "$VM_NAME" "$NEW_DATA_VOL_PATH" vdb --persistent --cache none
    log_info "Data volume attached as vdb"
fi

# Step 6: Update root disk path in VM XML
log_info "Step 6: Updating root disk path in VM XML..."

UPDATED_XML="/tmp/${VM_NAME}_update_$(date +%Y%m%d_%H%M%S).xml"
virsh dumpxml "$VM_NAME" > "$UPDATED_XML"

# Use the original volume path saved before step 4 (supports cross-pool migration)
# Replace old volume path regardless of quote style used by libvirt
sed -i "s|source file='${ORIG_ROOT_VOL_PATH}'|source file='${NEW_ROOT_VOL_PATH}'|g" "$UPDATED_XML"
sed -i "s|source file=\"${ORIG_ROOT_VOL_PATH}\"|source file=\"${NEW_ROOT_VOL_PATH}\"|g" "$UPDATED_XML"

virsh define "$UPDATED_XML"
rm -f "$UPDATED_XML"
log_info "Root disk path updated: ${ORIG_ROOT_VOL_PATH} -> ${NEW_ROOT_VOL_PATH}"

# Step 7: Rename VM
log_info "Step 7: Renaming VM..."
log_info "  $VM_NAME -> $NEW_VM_NAME"

virsh domrename "$VM_NAME" "$NEW_VM_NAME"
log_info "VM renamed to: $NEW_VM_NAME"

# Step 8: Add genesis:image metadata to VM XML
log_info "Step 8: Adding genesis:image metadata to VM..."

MODIFIED_XML="/tmp/${NEW_VM_NAME}_metadata.xml"
virsh dumpxml "$NEW_VM_NAME" > "$MODIFIED_XML"

IMAGE_TAG='    <genesis:image uri="https://repo.exordos.com/exordos-elements/core/0.1.0/images/exordos-core.raw.zst"/>'

python3 - "$MODIFIED_XML" "$IMAGE_TAG" "$NETWORK" "$CIDR" << 'PYEOF'
import re
import sys

xml_file = sys.argv[1]
image_tag = sys.argv[2]
network = sys.argv[3]
cidr = sys.argv[4]

with open(xml_file, 'r') as f:
    content = f.read()

# Add missing individual tags - check each tag separately
if 'genesis:image uri=' not in content:
    # Replace old-style <genesis:image>...</genesis:image> if present
    content = re.sub(r'<genesis:image>[^<]*</genesis:image>', image_tag, content)
    # If no genesis:image tag at all - insert before closing genesis:genesis
    if 'genesis:image' not in content:
        content = content.replace('</genesis:genesis>', '      ' + image_tag + '\n    </genesis:genesis>')

# Add missing vcpu tag
if '<genesis:vcpu>' not in content:
    content = content.replace('</genesis:genesis>', '      <genesis:vcpu>2</genesis:vcpu>\n    </genesis:genesis>')

# Add missing mem tag
if '<genesis:mem>' not in content:
    content = content.replace('</genesis:genesis>', '      <genesis:mem>4096</genesis:mem>\n    </genesis:genesis>')

# Add missing node_type tag
if '<genesis:node_type>' not in content:
    content = content.replace('</genesis:genesis>', '      <genesis:node_type>bootstrap</genesis:node_type>\n    </genesis:genesis>')

# Add missing network tag
if '<genesis:network' not in content:
    content = content.replace('</genesis:genesis>', f'      <genesis:network cidr="{cidr}" managed_network="0" dhcp="0">{network}</genesis:network>\n    </genesis:genesis>')

# Update stand tag if present
content = content.replace(
    '<genesis:stand>genesis-core</genesis:stand>',
    '<genesis:stand>exordos-core</genesis:stand>'
)

with open(xml_file, 'w') as f:
    f.write(content)
print('XML metadata updated successfully')
PYEOF

# Redefine VM with modified XML
virsh define "$MODIFIED_XML"
rm -f "$MODIFIED_XML"
log_info "Metadata added to VM"

# ================================================
# Summary: parameters for migration_inner
# ================================================
log_info "Step 9: Collecting parameters for inner migration script..."

VM_XML_FINAL=$(virsh dumpxml "$NEW_VM_NAME" 2>/dev/null)

VCPUS=$(echo "$VM_XML_FINAL" | grep -oP '<vcpu[^>]*>\K\d+' | head -1 || true)
MEMORY_KIB=$(echo "$VM_XML_FINAL" | grep -oP '(?<=<memory unit=.KiB.>)\d+' | head -1 || true)
if [[ -n "$MEMORY_KIB" ]]; then
    MEMORY_MB=$(( MEMORY_KIB / 1024 ))
else
    MEMORY_MB=""
fi

# Extract MAC addresses of all interfaces (in order)
MACS=$(echo "$VM_XML_FINAL" | grep -oP "(?<=<mac address=')[^']+" || true)
PORT_MAIN_MAC=$(echo "$MACS" | sed -n '1p')
PORT_BOOT_MAC=$(echo "$MACS" | sed -n '2p')

log_info "================================================"
log_info "Outer migration completed!"
log_info "================================================"
log_info ""
log_info "Parameters for migration_inner:"
log_info "  node-uuid      : $VM_UUID"
log_info "  machine-prefix : $PREFIX"
log_info "  ram            : ${MEMORY_MB} MB"
log_info "  core           : $VCPUS"
log_info "  disk-root-size : $ROOT_DISK_SIZE_GB GB"
log_info "  disk-data-size : $DATA_DISK_SIZE_GB GB"
log_info "  port-main-mac  : $PORT_MAIN_MAC"
log_info "  port-boot-mac  : $PORT_BOOT_MAC"
log_info ""
log_info "Run migration_inner inside the VM:"
log_info "------------------------------------------------"
echo ""
echo "bash migration_inner_0.1.X_phoenix.sh \\"
echo "    --node-uuid      \"$VM_UUID\" \\"
echo "    --machine-prefix \"$PREFIX\" \\"
echo "    --ram            \"$MEMORY_MB\" \\"
echo "    --core           \"$VCPUS\" \\"
echo "    --disk-root-size \"$ROOT_DISK_SIZE_GB\" \\"
echo "    --disk-data-size \"$DATA_DISK_SIZE_GB\" \\"
echo "    --port-main-mac  \"$PORT_MAIN_MAC\" \\"
echo "    --port-boot-mac  \"$PORT_BOOT_MAC\" \\"
echo "    --network        \"<NETWORK_NAME>\" \\"
echo "    --port-main-ip   \"<PORT_MAIN_IP>\""
echo ""
log_info "------------------------------------------------"
log_info "NOTE: fill in --network and --port-main-ip manually"
log_info "================================================"
