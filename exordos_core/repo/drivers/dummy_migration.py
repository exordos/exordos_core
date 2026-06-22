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

from restalchemy.dm import filters as ra_filters

import exordos_core.repo.dm.models as repo_models
from exordos_core.repo.drivers import base


class DummyMigrationRepoDriver(base.AbstractProxyRepoDriver):
    """Stub driver for migrating old installations without repo proxy.

    This driver does not provide any real repository functionality.
    It exists solely so that RepoElement records can be attached to
    a Repository during data migration.
    """

    def __init__(self, repository: repo_models.Repository) -> None:
        super().__init__(repository)
        if repository.driver_spec.KIND != "dummy_migration":
            raise ValueError(
                f"Unsupported driver spec kind: {repository.driver_spec.KIND!r}"
            )

    @property
    def repo_uri(self) -> str:
        """Return the URI of the repository."""
        return "dummy://migration"

    def artifact_uri(
        self, element_name: str, element_version: str, category: str, artifact_name: str
    ) -> str:
        """Return the URI of the artifact."""
        return ""

    def get_inventory(self) -> dict:
        """Get inventory of the repository from the database."""
        # No need to return elements — they are already stored in the database.
        return {"elements": {}}

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
        return element, []
