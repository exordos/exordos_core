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

from __future__ import annotations

import json
from pathlib import Path
import re
import typing as tp
import urllib.parse
import urllib.request

import yaml

from exordos_core.common import constants as c
from exordos_core.common.exceptions import ManifestNotFound


def _join_url(*parts: str) -> str:
    # Join URL parts ensuring single slashes
    base = parts[0]
    for p in parts[1:]:
        base = urllib.parse.urljoin(base.rstrip("/") + "/", p)
    return base


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": f"{c.GLOBAL_SERVICE_NAME}/1.0"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read()


def _extract_hrefs(html: str) -> list[str]:
    # Extract href values from simple directory listings
    return re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.IGNORECASE)


class Repository:
    def __init__(self, repository_url: str):
        self.repository_url = repository_url

    def element_url(
        self,
        manifest_name: str,
    ) -> str:
        return _join_url(self.repository_url, manifest_name)

    @classmethod
    def element_html(cls, element_url: str) -> str:
        try:
            element_html = _http_get(element_url).decode("utf-8", errors="ignore")
            return element_html
        except Exception as exc:
            raise ManifestNotFound(err=f"Element not found at {element_url}: {exc}")

    @classmethod
    def get_inventory_url(
        cls,
        element_url: str,
        version: str,
    ) -> str:
        return _join_url(element_url, version, "inventory.json")

    def check_repo(self) -> None:
        try:
            _http_get(self.repository_url).decode("utf-8", errors="ignore")
        except Exception as exc:
            raise ManifestNotFound(
                err=f"Failed to access repository: {self.repository_url}: {exc}"
            )

    def get_manifest(
        self, element_name: str, element_version: str | None = None
    ) -> dict:
        self.check_repo()

        element_url = self.element_url(element_name)

        if not element_version:
            latest_dir = "latest"
        else:
            latest_dir = element_version

        inventory_url = self.get_inventory_url(element_url, latest_dir)
        inventory = self._element_inventory(inventory_url)

        target_manifest_path = self.get_manifest_path_from_inventory(
            inventory, element_name, inventory_url
        )

        manifest_url = self._manifest_url(element_url, latest_dir, target_manifest_path)
        manifest = self.get_manifest_by_url(manifest_url)
        return manifest

    @classmethod
    def get_manifest_path_from_inventory(
        cls, inventory: dict[str, tp.Any], manifest_name: str, inventory_url: str
    ) -> str:
        target_manifest_path = None
        for manifest_path in inventory["manifests"]:
            stem = Path(manifest_path).stem
            if stem == manifest_name:
                target_manifest_path = manifest_path
        if target_manifest_path is None:
            raise ManifestNotFound(
                err=f"Manifest '{manifest_name}' not found in inventory at {inventory_url}"
            )
        return target_manifest_path

    @classmethod
    def get_manifest_by_url(cls, manifest_url: str) -> dict[str, tp.Any]:
        try:
            data = _http_get(manifest_url)
            manifest = yaml.safe_load(data)
            if not isinstance(manifest, dict):
                raise ManifestNotFound(
                    err=f"Manifest at {manifest_url} is not a YAML mapping"
                )
            return manifest
        except ManifestNotFound:
            raise
        except Exception as exc:
            raise ManifestNotFound(
                err=f"Failed to download or parse manifest at {manifest_url}: {exc}"
            )

    @classmethod
    def _element_inventory(cls, inventory_url: str) -> dict[str, tp.Any]:
        try:
            inventory = json.loads(_http_get(inventory_url))
            return inventory
        except Exception as exc:
            raise ManifestNotFound(
                err=f"Failed to download or parse inventory at {inventory_url}: {exc}"
            )

    @classmethod
    def _manifest_url(
        cls,
        element_url: str,
        version: str,
        manifest_name: str,
    ) -> str:
        return _join_url(element_url, version, "manifests/", manifest_name)
