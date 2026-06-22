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

import abc

import exordos_core.repo.dm.models as repo_models


class AbstractProxyRepoDriver(abc.ABC):
    def __init__(self, repository: repo_models.Repository) -> None:
        self._repository = repository

    @property
    @abc.abstractmethod
    def repo_uri(self) -> str:
        """Return the URI of the repository."""

    @abc.abstractmethod
    def get_inventory(self) -> dict:
        """Get inventory of the repository."""

    @abc.abstractmethod
    def get_element(
        self, name: str, version: str = "latest"
    ) -> repo_models.RepoElement:
        """Get repository element by name and version."""

    def can_upload_element(self, name: str, version: str) -> bool:
        """Check if element can be uploaded to repository."""
        return False

    def upload_element(self, element: repo_models.RepoElement) -> None:
        """Upload element to repository."""
        raise NotImplementedError("Upload is not supported for this repository")
