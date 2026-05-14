#    Copyright 2025 Genesis Corporation.
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

import json
import os
from pathlib import Path
import sys
import time

REPOSITORY_DIR = "/var/lib/repository"
ELEMENTS_DIRS = [
    "genesis-elements",
    "exordos-elements",
]
INVENTORY = "*/inventory.json"


def find_inventory_files(root_path):
    """Find all inventory.json files in manifests subfolders."""
    inventory_files = []
    root = Path(root_path)

    for inventory_file in root.rglob(INVENTORY):
        inventory_files.append(inventory_file)

    return inventory_files


def merge_inventories(inventory_files):
    """Merge all found inventory files into one list."""
    result_inventory = {"elements": {}}

    latest_version = None
    for file_path in inventory_files:
        try:
            with open(file_path, "r") as f:
                data = json.load(f)
            if data["name"] not in result_inventory["elements"]:
                result_inventory["elements"][data["name"]] = {}

            result_inventory["elements"][data["name"]][data["version"]] = data

            if latest_version is None or data["version"] > latest_version:
                latest_version = data["version"]
                result_inventory["elements"][data["name"]]["latest"] = data

        except Exception as e:
            print(f"Error reading {file_path}: {e}")

    result_inventory["timestamp"] = time.time()
    return result_inventory


def write_merged_inventory(merged_data, output_path):
    """Write merged inventory to output file."""
    try:
        with open(output_path, "w") as f:
            json.dump(merged_data, f, indent=2)
        print(f"Merged inventory written to {output_path}")
    except Exception as e:
        print(f"Error writing merged inventory: {e}")
        sys.exit(1)


def main():
    for elements_dir in ELEMENTS_DIRS:
        root_dir = os.path.join(REPOSITORY_DIR, elements_dir)
        output_file = os.path.join(root_dir, "inventory.json")

        print(f"Searching for inventory.json files in {root_dir}...")
        inventory_files = find_inventory_files(root_dir)

        if not inventory_files:
            print("No inventory.json files found.")
            return

        print(f"Found {len(inventory_files)} inventory.json files.")

        merged_data = merge_inventories(inventory_files)
        print(f"Merged {len(merged_data['elements'])} items.")

        write_merged_inventory(merged_data, output_file)


if __name__ == "__main__":
    main()
