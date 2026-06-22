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

import typing as tp
import uuid as sys_uuid

from restalchemy.dm import filters as ra_filters

import exordos_core.repo.dm.models as repo_models
from exordos_core.repo.drivers import base


class DatabaseProxyRepoDriver(base.AbstractProxyRepoDriver):
    """Driver for database-backed repositories with upload support.

    Stores uploaded elements directly in the database and dynamically
    builds inventory from the stored elements.
    """

    def __init__(self, repository: repo_models.Repository) -> None:
        super().__init__(repository)
        if repository.driver_spec.KIND != "database":
            raise ValueError(
                f"Unsupported driver spec kind: {repository.driver_spec.KIND!r}"
            )

    @property
    def repo_uri(self) -> str:
        """Return the URI of the repository."""
        return ""

    def artifact_uri(
        self, element_name: str, element_version: str, category: str, artifact_name: str
    ) -> str:
        """Return the URI of the artifact."""
        return ""

    def get_inventory(self) -> dict:
        """Get inventory of the repository from the database."""
        elements = repo_models.RepoElement.objects.get_all(
            filters={
                "repository": ra_filters.EQ(self._repository.uuid),
            }
        )

        inventory: dict[str, dict] = {"elements": {}}
        for element in elements:
            if element.name not in inventory["elements"]:
                inventory["elements"][element.name] = {}
            inventory["elements"][element.name][element.version] = (
                element.inventory or self._build_inventory_from_element(element)
            )

        return inventory

    def get_element(
        self, name: str, version: str = "latest"
    ) -> tuple[repo_models.RepoElement, tp.Collection[repo_models.RepoArtifact]]:
        """Get repository element by name and version from the database."""
        element = repo_models.RepoElement.objects.get_one(
            filters={
                "name": ra_filters.EQ(name),
                "version": ra_filters.EQ(version),
                "repository": ra_filters.EQ(self._repository.uuid),
            }
        )

        artifacts = repo_models.RepoArtifact.objects.get_all(
            filters={
                "element": ra_filters.EQ(element.uuid),
            }
        )

        return element, artifacts

    def can_upload_element(self, name: str, version: str) -> bool:
        """Check if element can be uploaded to repository."""
        existing = repo_models.RepoElement.objects.get_all(
            filters={
                "name": ra_filters.EQ(name),
                "version": ra_filters.EQ(version),
                "repository": ra_filters.EQ(self._repository.uuid),
            }
        )
        return not existing

    def upload_element(self, element: repo_models.RepoElement) -> None:
        """Upload element to repository.

        Builds the inventory from the manifest and sets it on the element.
        The element itself is saved by the caller (``Repository.upload``).
        """
        element.inventory = self._build_inventory_from_element(element)

    @staticmethod
    def _build_inventory_from_element(
        element: repo_models.RepoElement,
    ) -> dict:
        """Build inventory dict from a stored element.

        If the element already has an inventory, return it as-is.
        Otherwise, build a minimal inventory that includes the manifest
        as a manifest entry.
        """
        if element.inventory:
            return element.inventory

        manifest_name = f"{element.name}.yaml"
        manifest_uuid = str(sys_uuid.uuid4())

        return {
            "name": element.name,
            "version": element.version,
            "artifacts": [],
            "configs": [],
            "images": [],
            "manifests": [manifest_name],
            "templates": [],
            "index": {
                "artifacts": {},
                "configs": {},
                "images": {},
                "manifests": {manifest_uuid: manifest_name},
                "templates": {},
            },
        }
