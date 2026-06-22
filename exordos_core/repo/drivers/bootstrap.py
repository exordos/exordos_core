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

import logging
import os
import typing as tp

import yaml

import exordos_core.repo.dm.models as repo_models
from exordos_core.repo.drivers import base

LOG = logging.getLogger(__name__)


class BootstrapProxyRepoDriver(base.AbstractProxyRepoDriver):
    """Driver for bootstrap repository that reads manifests from local directory."""

    def __init__(self, repository: repo_models.Repository) -> None:
        super().__init__(repository)
        if repository.driver_spec.KIND != "bootstrap":
            raise ValueError(
                f"Unsupported driver spec kind: {repository.driver_spec.KIND!r}"
            )

        spec = repository.driver_spec
        self._manifests_dir = spec.manifests_dir
        self._manifests_cache = {}
        self._inventory_cache = {"elements": {}}
        self._scan_manifests()

    @property
    def repo_uri(self) -> str:
        """Return the URI of the repository."""
        return self._manifests_dir

    def _scan_manifests(self) -> None:
        """Scan manifests directory and cache valid manifests.

        A file is considered a valid manifest if:
        - It has .yaml or .yml extension
        - The YAML can be loaded successfully
        - The loaded data is a dictionary
        - It contains 'name', 'version', and 'resources' keys
        """
        if not os.path.exists(self._manifests_dir):
            raise FileNotFoundError(
                f"Manifests directory {self._manifests_dir} does not exist"
            )

        for filename in os.listdir(self._manifests_dir):
            if not filename.endswith(".yaml") and not filename.endswith(".yml"):
                continue

            filepath = os.path.join(self._manifests_dir, filename)
            try:
                with open(filepath) as f:
                    manifest_data = yaml.safe_load(f)
            except Exception:
                LOG.debug("Failed to load YAML from %s", filepath)
                continue

            # Check if this is a valid manifest
            if not isinstance(manifest_data, dict):
                continue
            required_keys = {"name", "version", "resources"}
            if required_keys - manifest_data.keys():
                continue

            name = manifest_data["name"]
            version = manifest_data["version"]

            # Cache the manifest
            self._manifests_cache[(name, version)] = manifest_data

            # Update inventory
            if name not in self._inventory_cache["elements"]:
                self._inventory_cache["elements"][name] = {}
            if version not in self._inventory_cache["elements"][name]:
                self._inventory_cache["elements"][name][version] = {
                    "artifacts": [],
                    "configs": [],
                    "images": [],
                    "manifests": [filename],
                    "name": name,
                    "templates": [],
                    "version": version,
                    "index": {},
                }

            LOG.info("Loaded manifest %s:%s from %s", name, version, filepath)

    def get_inventory(self) -> dict:
        """Get inventory of the repository."""
        return self._inventory_cache

    def artifact_uri(
        self, element_name: str, element_version: str, category: str, artifact_name: str
    ) -> str:
        """Return the URI of the artifact."""
        return os.path.join(self._manifests_dir, artifact_name)

    def get_element(
        self, name: str, version: str = "latest"
    ) -> tuple[repo_models.RepoElement, tp.Collection[repo_models.RepoArtifact]]:
        """Get repository element by name and version."""
        cache_key = (name, version)
        if cache_key not in self._manifests_cache:
            raise ValueError(
                f"Element {name}:{version} not found in bootstrap repository"
            )

        manifest = self._manifests_cache[cache_key]
        inventory = self._inventory_cache["elements"][name][version]

        element, artifacts = repo_models.RepoElement.from_inventory(
            self._repository, inventory
        )
        element.manifest = manifest
        element.description = manifest.get("description", "")
        return element, artifacts
