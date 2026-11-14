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
import sys
import uuid

DATA_DEVICE = "/dev/vdb"


def mounted(devices):
    return any(
        any(device.get("mountpoints") or []) or mounted(device.get("children", []))
        for device in devices
    )


def prepare(ost_uuid):
    volume_group = f"rawstor-{uuid.UUID(str(ost_uuid))}"
    info = json.loads(
        subprocess.check_output(
            ["lsblk", "--json", "--output", "NAME,TYPE,MOUNTPOINTS", DATA_DEVICE]
        )
    )
    devices = info["blockdevices"]
    if len(devices) != 1 or devices[0]["type"] != "disk" or mounted(devices):
        raise RuntimeError("OST data device is missing or mounted")

    report = json.loads(
        subprocess.check_output(
            ["pvs", "--reportformat", "json", "-o", "pv_name,vg_name"]
        )
    )
    physical_volumes = [pv for entry in report["report"] for pv in entry["pv"]]
    data_pv = None
    for pv in physical_volumes:
        device = os.path.realpath(pv["pv_name"])
        group = pv["vg_name"].strip()
        if group == volume_group and device != DATA_DEVICE:
            raise RuntimeError(
                "OST volume group does not use only the designated data device"
            )
        if device == DATA_DEVICE:
            if group not in ("", volume_group):
                raise RuntimeError("OST data device belongs to another volume group")
            data_pv = pv

    if data_pv is None:
        probe = subprocess.run(["blkid", "-p", DATA_DEVICE], capture_output=True)
        if probe.returncode != 2:
            raise RuntimeError("OST data device is not empty or cannot be inspected")
        subprocess.run(["pvcreate", DATA_DEVICE], check=True)
    if data_pv is None or not data_pv["vg_name"].strip():
        subprocess.run(["vgcreate", volume_group, DATA_DEVICE], check=True)
    subprocess.run(["vgchange", "--activate", "y", volume_group], check=True)


if __name__ == "__main__":
    prepare(sys.argv[1])
