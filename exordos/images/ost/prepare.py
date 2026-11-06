#!/usr/bin/env python3
#    Copyright 2026 Genesis Corporation.
#
#    All Rights Reserved.
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

"""Prepare only the designated data disk; never force-create over existing data."""

import json
import os
import subprocess

DATA_DEVICE = "/dev/vdb"
POOL = "rawstor-ost"
DATASET = f"{POOL}/data"


def mounted(devices):
    return any(
        any(device.get("mountpoints") or []) or mounted(device.get("children", []))
        for device in devices
    )


def prepare():
    subprocess.run(["modprobe", "zfs"], check=True)
    if subprocess.run(
        ["zpool", "list", "-H", "-o", "name", POOL], capture_output=True
    ).returncode:
        imported = subprocess.run(["zpool", "import", "-N", POOL], capture_output=True)
        if imported.returncode:
            info = json.loads(
                subprocess.check_output(
                    [
                        "lsblk",
                        "--json",
                        "--output",
                        "NAME,TYPE,MOUNTPOINTS",
                        DATA_DEVICE,
                    ]
                )
            )
            devices = info["blockdevices"]
            if len(devices) != 1 or devices[0]["type"] != "disk" or mounted(devices):
                raise RuntimeError("OST data device is missing or mounted")
            probe = subprocess.run(["blkid", "-p", DATA_DEVICE], capture_output=True)
            if probe.returncode != 2:
                raise RuntimeError(
                    "OST data device is not empty or cannot be inspected"
                )
            subprocess.run(
                [
                    "zpool",
                    "create",
                    "-o",
                    "ashift=12",
                    "-O",
                    "compression=lz4",
                    "-O",
                    "mountpoint=none",
                    POOL,
                    DATA_DEVICE,
                ],
                check=True,
            )
    status = subprocess.check_output(["zpool", "status", "-P", POOL], text=True)
    devices = {
        os.path.realpath(word) for word in status.split() if word.startswith("/dev/")
    }
    if not devices or not devices <= {DATA_DEVICE, f"{DATA_DEVICE}1"}:
        raise RuntimeError("OST ZFS pool does not use the designated data device")
    if subprocess.run(
        ["zfs", "list", "-H", "-o", "name", DATASET], capture_output=True
    ).returncode:
        subprocess.run(["zfs", "create", "-o", "mountpoint=none", DATASET], check=True)


if __name__ == "__main__":
    prepare()
