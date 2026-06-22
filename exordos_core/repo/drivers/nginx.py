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
from urllib.parse import urljoin
from urllib.parse import urlparse

import bazooka
from requests import auth as requests_auth
import yaml

import exordos_core.repo.dm.models as repo_models
from exordos_core.repo.drivers import base

_ALLOWED_SCHEMES = frozenset({"http", "https"})


class NginxProxyRepoDriver(base.AbstractProxyRepoDriver):
    def __init__(self, repository: repo_models.Repository) -> None:
        super().__init__(repository)
        if repository.driver_spec.KIND != "nginx":
            raise ValueError(
                f"Unsupported driver spec kind: {repository.driver_spec.KIND!r}"
            )

        spec = repository.driver_spec
        self._validate_url(spec.url)

        auth = None
        if spec.username is not None:
            auth = requests_auth.HTTPBasicAuth(spec.username, spec.password)
        self._client = bazooka.Client(default_timeout=30, auth=auth)
        self._spec = spec

    @staticmethod
    def _validate_url(url: str) -> None:
        """Validate repository URL scheme to prevent SSRF attacks."""
        parsed = urlparse(url)
        if parsed.scheme not in _ALLOWED_SCHEMES:
            raise ValueError(
                f"Unsupported URL scheme: {parsed.scheme!r}. "
                f"Allowed: {', '.join(sorted(_ALLOWED_SCHEMES))}"
            )

    def _get(self, url: str) -> dict:
        response = self._client.get(url)
        response.raise_for_status()
        return response.json()

    @property
    def repo_uri(self) -> str:
        """Return the URI of the repository."""
        return self._spec.url

    def artifact_uri(
        self, element_name: str, element_version: str, category: str, artifact_name: str
    ) -> str:
        """Return the URI of the artifact.

        Builds the full URL using the category and artifact name:
        ``{url}/{name}/{version}/{category}/{artifact_name}``.
        """
        element_base_url = urljoin(self._spec.url, f"{element_name}/{element_version}/")
        return urljoin(element_base_url, f"{category}/{artifact_name}")

    def get_inventory(self) -> dict:
        """Get inventory of the repository."""
        return self._get(self._spec.inventory_path)

    def get_element(
        self, name: str, version: str = "latest"
    ) -> tuple[repo_models.RepoElement, tp.Collection[repo_models.RepoArtifact]]:
        """Get repository element by name and version."""
        element_base_url = urljoin(self._spec.url, f"{name}/{version}/")
        manifest_response = self._client.get(
            urljoin(element_base_url, f"manifests/{name}.yaml")
        )
        manifest_response.raise_for_status()
        manifest = yaml.safe_load(manifest_response.text)
        inventory = self._get(urljoin(element_base_url, "inventory.json"))

        element, artifacts = repo_models.RepoElement.from_inventory(
            self._repository, inventory
        )
        element.manifest = manifest
        element.description = manifest.get("description", "")
        return element, artifacts
