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
VENV_PATH="$GC_PATH/.venv"

# Disk auto-provisioning for Exordos data directory.
#
# Goal:
# - Detect the "secondary" disk (any disk that is not the root disk)
# - If it has no partitions: create a single GPT partition
# - If the partition has no filesystem: format it as ext4
# - Ensure it is mounted at /var/lib/exordos/data
# - Ensure a persistent /etc/fstab entry using UUID
#
# This block is intended to be idempotent and safe to run on every boot.

log() {
  echo "[exordos-bootstrap] $*"
}

host_mountpoint() {
  if command -v nsenter >/dev/null 2>&1; then
    nsenter -t 1 -m -- mountpoint -q "$1"
    return $?
  fi

  mountpoint -q "$1"
}

# Identify the block device that backs the root filesystem.
# Example output:
#   [exordos-bootstrap] root source: /dev/vda1
#   [exordos-bootstrap] root disk: vda
ROOT_SRC="$(findmnt -n -o SOURCE / || true)"
ROOT_DISK=""
if [[ -n "${ROOT_SRC}" ]]; then
  ROOT_DISK="$(lsblk -no PKNAME "${ROOT_SRC}" 2>/dev/null || true)"
fi
log "root source: ${ROOT_SRC:-<unknown>}"
log "root disk: ${ROOT_DISK:-<unknown>}"

# Pick the first disk device that is not the root disk.
# Example output:
#   [exordos-bootstrap] secondary disk candidate: vdb
SECOND_DISK="$(lsblk -dn -o NAME,TYPE | awk -v root_disk="${ROOT_DISK}" '$2=="disk" && $1!=root_disk {print $1; exit}')"
log "secondary disk candidate: ${SECOND_DISK:-<none>}"

if [[ -n "${SECOND_DISK}" ]]; then
  DISK_DEV="/dev/${SECOND_DISK}"

  # Count partitions on the selected disk.
  # Example output:
  #   [exordos-bootstrap] /dev/vdb partitions: 0
  PART_COUNT="$(lsblk -nr -o TYPE "${DISK_DEV}" | awk '$1=="part" {c++} END {print c+0}')"
  log "${DISK_DEV} partitions: ${PART_COUNT}"

  # Create a single partition when there are no partitions yet.
  # Example output:
  #   [exordos-bootstrap] creating GPT partition table and one partition on /dev/vdb
  if [[ "${PART_COUNT}" -eq 0 ]]; then
    log "creating GPT partition table and one partition on ${DISK_DEV}"
    sfdisk --label gpt "${DISK_DEV}" <<'EOF'
,,
EOF
    partprobe "${DISK_DEV}" || true
    udevadm settle || true
  else
    log "${DISK_DEV} already has partitions; skipping partition creation"
  fi

  # Pick the first partition on the disk.
  # Example output:
  #   [exordos-bootstrap] selected partition: /dev/vdb1
  PART_NAME="$(lsblk -nr -o NAME,TYPE "${DISK_DEV}" | awk '$2=="part" {print $1; exit}')"
  if [[ -n "${PART_NAME}" ]]; then
    PART_DEV="/dev/${PART_NAME}"
    log "selected partition: ${PART_DEV}"

    # Detect filesystem type; create ext4 if unformatted.
    # Example output:
    #   [exordos-bootstrap] filesystem on /dev/vdb1: <none>
    #   [exordos-bootstrap] formatting /dev/vdb1 as ext4
    FS_TYPE="$(blkid -o value -s TYPE "${PART_DEV}" 2>/dev/null || true)"
    log "filesystem on ${PART_DEV}: ${FS_TYPE:-<none>}"
    if [[ -z "${FS_TYPE}" ]]; then
      log "formatting ${PART_DEV} as ext4"
      mkfs.ext4 -F "${PART_DEV}"
      FS_TYPE="ext4"
    else
      log "${PART_DEV} already has filesystem '${FS_TYPE}'; skipping mkfs"
    fi

    # Mount and persist only if it is ext4.
    if [[ "${FS_TYPE}" == "ext4" ]]; then
      MOUNTPOINT="/var/lib/exordos/data"

      # Ensure mountpoint exists.
      # Example output:
      #   [exordos-bootstrap] ensuring mountpoint exists: /var/lib/exordos/data
      mkdir -p "${MOUNTPOINT}"
      log "ensuring mountpoint exists: ${MOUNTPOINT}"

      # Ensure /etc/fstab has the correct UUID-based entry.
      # Example output:
      #   [exordos-bootstrap] partition UUID: 1234-...
      #   [exordos-bootstrap] updating /etc/fstab entry for /var/lib/exordos/data
      UUID="$(blkid -o value -s UUID "${PART_DEV}" 2>/dev/null || true)"
      if [[ -n "${UUID}" ]]; then
        log "partition UUID: ${UUID}"
        if grep -qs "[[:space:]]${MOUNTPOINT}[[:space:]]" /etc/fstab; then
          if ! grep -qs "^[[:space:]]*UUID=${UUID}[[:space:]].*[[:space:]]${MOUNTPOINT}[[:space:]]" /etc/fstab; then
            log "updating /etc/fstab entry for ${MOUNTPOINT}"
            TMP_FSTAB="$(mktemp)"
            awk -v mp="${MOUNTPOINT}" '!(($2==mp)) {print}' /etc/fstab > "${TMP_FSTAB}"
            printf '%s\n' "UUID=${UUID} ${MOUNTPOINT} ext4 defaults,nofail 0 2" >> "${TMP_FSTAB}"
            mv "${TMP_FSTAB}" /etc/fstab

            # Reload systemd units generated from fstab so the mount can work on first run.
            # Example output:
            #   [exordos-bootstrap] running: systemctl daemon-reload
            log "running: systemctl daemon-reload"
            systemctl daemon-reload || true
          else
            log "/etc/fstab already has the correct UUID entry for ${MOUNTPOINT}"
          fi
        else
          log "adding /etc/fstab entry for ${MOUNTPOINT}"
          printf '%s\n' "UUID=${UUID} ${MOUNTPOINT} ext4 defaults,nofail 0 2" >> /etc/fstab

          # Reload systemd units generated from fstab so the mount can work on first run.
          # Example output:
          #   [exordos-bootstrap] running: systemctl daemon-reload
          log "running: systemctl daemon-reload"
          systemctl daemon-reload || true
        fi
      else
        log "could not determine UUID for ${PART_DEV}; skipping fstab update"
      fi

      # Mount if not mounted yet.
      # Example output:
      #   [exordos-bootstrap] mounting /var/lib/exordos/data
      if ! host_mountpoint "${MOUNTPOINT}"; then
        log "mounting ${MOUNTPOINT}"
        mount "${MOUNTPOINT}" || mount "${PART_DEV}" "${MOUNTPOINT}"

        # Some systemd units may run with an isolated mount namespace (e.g. PrivateMounts=yes).
        # In that case, a mount performed here may not be visible from the host namespace.
        # If it is still not mounted, retry in PID 1 mount namespace.
        # Example output:
        #   [exordos-bootstrap] mount not visible after mount; retrying in PID 1 mount namespace
        if ! host_mountpoint "${MOUNTPOINT}"; then
          if command -v nsenter >/dev/null 2>&1; then
            log "mount not visible from host namespace; retrying in PID 1 mount namespace"
            nsenter -t 1 -m -- mount "${MOUNTPOINT}" || nsenter -t 1 -m -- mount "${PART_DEV}" "${MOUNTPOINT}" || true
          fi
        fi

        if host_mountpoint "${MOUNTPOINT}"; then
          log "mounted successfully: ${MOUNTPOINT}"
        else
          log "mount failed or not visible: ${MOUNTPOINT}"
        fi
      else
        log "${MOUNTPOINT} is already mounted; skipping"
      fi
    else
      log "filesystem on ${PART_DEV} is '${FS_TYPE}', not ext4; skipping mount setup"
    fi
  else
    log "no partition found on ${DISK_DEV}; skipping"
  fi
else
  log "no secondary disk detected; skipping data disk provisioning"
fi


# Execution can continue only if the secondary disk was detected and is mounted at /var/lib/exordos/data.
if [[ -z "${SECOND_DISK}" ]]; then
  echo "[exordos-bootstrap] ERROR: secondary disk was not detected; refusing to continue" >&2
  exit 1
fi

if ! host_mountpoint "/var/lib/exordos/data"; then
  echo "[exordos-bootstrap] ERROR: /var/lib/exordos/data is not mounted; refusing to continue" >&2
  exit 1
fi

# PostgreSQL data relocation and exordos_core DB bootstrap.
#
# Goal:
# - Keep PostgreSQL packages installation logic in install.sh
# - Ensure PostgreSQL data files live under /var/lib/exordos/data so the disk can be moved

if host_mountpoint "/var/lib/exordos/data"; then
  if command -v psql >/dev/null 2>&1; then
    GC_PG_USER="${GC_PG_USER:-exordos_core}"
    GC_PG_PASS="${GC_PG_PASS:-exordos_core}"
    GC_PG_DB="${GC_PG_DB:-exordos_core}"

    PG_VERSION_DIR=""
    if [[ -d /etc/postgresql ]]; then
      PG_VERSION_DIR="$(ls -1 /etc/postgresql 2>/dev/null | sort -V | tail -n 1 || true)"
    fi

    if [[ -n "${PG_VERSION_DIR}" ]]; then
      PG_CONF_DIR="/etc/postgresql/${PG_VERSION_DIR}/main"
      PG_CONF_FILE="${PG_CONF_DIR}/postgresql.conf"
      OLD_PGDATA="/var/lib/postgresql/${PG_VERSION_DIR}/main"
      NEW_PGDATA="/var/lib/exordos/data/postgresql/${PG_VERSION_DIR}/main"

      log "postgresql version detected: ${PG_VERSION_DIR}"
      log "postgresql old data dir: ${OLD_PGDATA}"
      log "postgresql new data dir: ${NEW_PGDATA}"

      if [[ -f "${PG_CONF_FILE}" ]]; then
        if ! grep -qs "^[[:space:]]*data_directory[[:space:]]*=[[:space:]]*'${NEW_PGDATA}'" "${PG_CONF_FILE}"; then
          log "configuring PostgreSQL data_directory to ${NEW_PGDATA}"

          systemctl stop postgresql || true
          mkdir -p "${NEW_PGDATA}"
          chown -R postgres:postgres "/var/lib/exordos/data/postgresql" || true

          if [[ -d "${OLD_PGDATA}" && ! -f "${NEW_PGDATA}/PG_VERSION" ]]; then
            log "copying PostgreSQL data directory to ${NEW_PGDATA}"
            MAX_RETRIES=5
            RETRY_COUNT=0
            while [[ ${RETRY_COUNT} -lt ${MAX_RETRIES} ]]; do
              if rsync -aHAX --numeric-ids "${OLD_PGDATA}/" "${NEW_PGDATA}/"; then
                log "rsync completed successfully"
                break
              else
                RETRY_COUNT=$((RETRY_COUNT + 1))
                if [[ ${RETRY_COUNT} -lt ${MAX_RETRIES} ]]; then
                  log "rsync failed, retrying in 0.5s (attempt ${RETRY_COUNT}/${MAX_RETRIES})"
                  sleep 0.5
                else
                  log "ERROR: rsync failed after ${MAX_RETRIES} attempts"
                  exit 1
                fi
              fi
            done
            chown -R postgres:postgres "${NEW_PGDATA}" || true
          else
            log "PostgreSQL data directory already present under ${NEW_PGDATA}; skipping copy"
          fi

          if grep -qs "^[[:space:]]*data_directory[[:space:]]*=" "${PG_CONF_FILE}"; then
            sed -i "s|^[[:space:]]*data_directory[[:space:]]*=.*|data_directory = '${NEW_PGDATA}'|" "${PG_CONF_FILE}"
          else
            printf '%s\n' "data_directory = '${NEW_PGDATA}'" >> "${PG_CONF_FILE}"
          fi

          systemctl daemon-reload || true
          systemctl start postgresql || true
        else
          log "PostgreSQL is already configured to use ${NEW_PGDATA}"
        fi

        systemctl start postgresql || true
      else
        log "PostgreSQL config not found at ${PG_CONF_FILE}; skipping PostgreSQL relocation"
      fi
    else
      log "PostgreSQL version directory not found under /etc/postgresql; skipping PostgreSQL relocation"
    fi
  else
    log "psql is not available; skipping PostgreSQL relocation"
  fi
else
  log "/var/lib/exordos/data is not mounted; skipping PostgreSQL relocation"
fi

# Additional PostgreSQL configuration
sudo -u postgres psql -c "ALTER SYSTEM SET io_method = 'io_uring';"

# Mount CD-ROM if device is present.
CDROM_DEV="$(lsblk -dn -o NAME,TYPE | awk '$2=="rom" {print "/dev/"$1; exit}')"
CDROM_MOUNTPOINT="/mnt/cdrom"
if [[ -n "${CDROM_DEV}" ]]; then
  log "cd-rom device detected: ${CDROM_DEV}"
  mkdir -p "${CDROM_MOUNTPOINT}"
  if mountpoint -q "${CDROM_MOUNTPOINT}"; then
    log "cd-rom is already mounted at ${CDROM_MOUNTPOINT}"
  else
    if mount -o ro "${CDROM_DEV}" "${CDROM_MOUNTPOINT}"; then
      log "cd-rom mounted at ${CDROM_MOUNTPOINT}"
    else
      log "failed to mount cd-rom ${CDROM_DEV} at ${CDROM_MOUNTPOINT}"
    fi
  fi
else
  log "cd-rom device not detected"
fi

# Prepare templated configuration files and apply them
log "ec-bootstrap-templates"
sudo ec-bootstrap-templates
log "netplan apply"
sudo netplan apply
log "systemctl restart systemd-resolved.service dnsdist@private.service"
sudo systemctl restart \
  systemd-resolved.service \
  dnsdist@private.service

log "Apply migrations"
# Apply migrations
source "$VENV_PATH/bin/activate"
# TODO(akremenetsky): Database configuration parameters should be taken
# from persistent configuration file.
ra-apply-migration --config-dir "$GC_CFG_DIR/" --path "$GC_PATH/migrations"

# Enable exordos core services
log "systemctl enable --now ec-services"
sudo systemctl enable --now \
    ec-user-api \
    ec-orch-api \
    ec-status-api \
    ec-boot-api \
    ec-gservice \
    ec-core-agent \
    exordos-universal-agent \
    exordos-universal-scheduler

# Perform the bootstrap of GC
log "Perform the bootstrap of GC"
ec-bootstrap --config-file /etc/exordos_core/exordos_core.conf


# Configure NAT
log "Configure NAT"
echo "net.ipv4.ip_forward=1" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p

# Configure iptables
log "Configure iptables"
sudo iptables -t nat -A POSTROUTING -o enp1s0 -j MASQUERADE

# Save iptables rules
log "Save iptables rules"
sudo netfilter-persistent save
