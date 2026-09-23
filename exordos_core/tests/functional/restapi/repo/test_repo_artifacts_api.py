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

import uuid as sys_uuid

from bazooka import exceptions as bazooka_exc
import pytest
from restalchemy.dm import filters as dm_filters

from exordos_core.common import constants as c
from exordos_core.common import utils
from exordos_core.repo.dm import models as repo_models

REPO_ARTIFACTS_READ = "repo.artifact.read"


def _activate_repository(repo_uuid):
    """Mark a repository ACTIVE.

    The API tests run without the repository builder, so a created
    repository stays NEW, and uploads are rejected until it is ACTIVE.
    """
    repository = repo_models.Repository.objects.get_one(
        filters={"uuid": dm_filters.EQ(repo_uuid)}
    )
    repository.status = repo_models.RepositoryStatus.ACTIVE.value
    repository.update()


class TestRepoArtifacts:
    """REST API tests for the repo artifacts endpoint."""

    REPO_ARTIFACTS_PATH = ["repo", "artifacts"]
    REPO_REPOSITORIES_PATH = ["repo", "repositories"]
    REPO_ELEMENTS_PATH = ["repo", "elements"]

    def _create_repository(
        self, user_api_client, auth, name="test-repo", driver_spec=None
    ):
        client = user_api_client(auth)
        url = client.build_collection_uri(self.REPO_REPOSITORIES_PATH)
        name = f"{name}-{sys_uuid.uuid4()}"
        response = client.post(
            url,
            json={
                "name": name,
                "description": "Test repository",
                "project_id": str(c.EM_PROJECT_ID),
                "sync_mode": "lazy",
                "driver_spec": driver_spec or {"kind": "database"},
            },
        )
        repository = response.json()
        _activate_repository(repository["uuid"])
        return repository

    def _create_element(
        self, user_api_client, auth, repo_uuid, name="test-element", version="1.0.0"
    ):
        client = user_api_client(auth)
        upload_url = client.build_resource_uri(
            ["repo/repositories", repo_uuid, "actions/upload/invoke"]
        )
        name = f"{name}-{sys_uuid.uuid4()}"
        client.post(
            upload_url,
            json={
                "element_name": name,
                "element_version": version,
                "description": "Test element",
                "manifest": {"name": name, "version": version, "resources": {}},
            },
        )
        elements_url = client.build_collection_uri(self.REPO_ELEMENTS_PATH)
        elements = client.get(
            elements_url,
            params={"repository": repo_uuid},
        ).json()
        return next(
            element
            for element in elements
            if element["name"] == name and element["version"] == version
        )

    def _create_artifact(self, element_uuid, category="manifests"):
        """Create a RepoArtifact row directly via the ORM.

        The upload flow does not persist artifacts, so they are seeded
        here the same way ``RepoElement.from_inventory`` would.
        """
        element = repo_models.RepoElement.objects.get_one(
            filters={"uuid": dm_filters.EQ(element_uuid)}
        )
        artifact = repo_models.RepoArtifact(
            element=element,
            urn=utils.urn(category, str(sys_uuid.uuid4())),
            uri=f"file:///artifacts/{element.name}/{element.version}/{category}",
            project_id=element.project_id,
        )
        artifact.save()
        return artifact

    # ------------------------------------------------------------------
    # GET / FILTER tests
    # ------------------------------------------------------------------

    def test_get_artifact_by_admin(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)

        repo = self._create_repository(user_api_client, auth_user_admin)
        element = self._create_element(user_api_client, auth_user_admin, repo["uuid"])
        artifact = self._create_artifact(element["uuid"])

        artifact_url = client.build_resource_uri(["repo/artifacts", str(artifact.uuid)])
        result = client.get(artifact_url).json()
        assert result["uuid"] == str(artifact.uuid)
        assert result["urn"] == artifact.urn
        assert result["uri"] == artifact.uri

    def test_get_nonexistent_artifact(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)
        artifact_url = client.build_resource_uri(
            ["repo/artifacts", str(sys_uuid.uuid4())]
        )

        with pytest.raises(bazooka_exc.NotFoundError):
            client.get(artifact_url)

    def test_list_artifacts(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)

        repo = self._create_repository(user_api_client, auth_user_admin)
        element = self._create_element(user_api_client, auth_user_admin, repo["uuid"])
        first = self._create_artifact(element["uuid"])
        second = self._create_artifact(element["uuid"])

        artifacts_url = client.build_collection_uri(self.REPO_ARTIFACTS_PATH)
        artifacts = client.get(artifacts_url).json()
        returned_uuids = {a["uuid"] for a in artifacts}
        assert str(first.uuid) in returned_uuids
        assert str(second.uuid) in returned_uuids

    def test_filter_artifacts_by_element(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)

        repo = self._create_repository(user_api_client, auth_user_admin)
        element_a = self._create_element(
            user_api_client, auth_user_admin, repo["uuid"], name="elem-a"
        )
        element_b = self._create_element(
            user_api_client, auth_user_admin, repo["uuid"], name="elem-b"
        )
        self._create_artifact(element_a["uuid"])
        self._create_artifact(element_b["uuid"])

        artifacts_url = client.build_collection_uri(self.REPO_ARTIFACTS_PATH)
        artifacts = client.get(
            artifacts_url, params={"element": element_a["uuid"]}
        ).json()

        assert len(artifacts) == 1
        assert artifacts[0]["element"].endswith(element_a["uuid"])

    # ------------------------------------------------------------------
    # Authorization tests
    # ------------------------------------------------------------------

    def test_get_artifact_by_user_without_permissions(
        self, user_api_client, auth_test1_user, auth_user_admin
    ):
        client = user_api_client(auth_test1_user)

        repo = self._create_repository(user_api_client, auth_user_admin)
        element = self._create_element(user_api_client, auth_user_admin, repo["uuid"])
        artifact = self._create_artifact(element["uuid"])

        artifact_url = client.build_resource_uri(["repo/artifacts", str(artifact.uuid)])
        with pytest.raises(bazooka_exc.ForbiddenError):
            client.get(artifact_url)

    def test_get_artifact_by_user_with_read_permission(
        self, user_api_client, auth_test1_user, auth_user_admin
    ):
        client = user_api_client(auth_test1_user, permissions=[REPO_ARTIFACTS_READ])

        repo = self._create_repository(user_api_client, auth_user_admin)
        element = self._create_element(user_api_client, auth_user_admin, repo["uuid"])
        artifact = self._create_artifact(element["uuid"])

        artifact_url = client.build_resource_uri(["repo/artifacts", str(artifact.uuid)])
        result = client.get(artifact_url).json()
        assert result["uuid"] == str(artifact.uuid)

    def test_filter_artifacts_by_user_without_permissions(
        self, user_api_client, auth_test1_user, auth_user_admin
    ):
        client = user_api_client(auth_test1_user)

        repo = self._create_repository(user_api_client, auth_user_admin)
        element = self._create_element(user_api_client, auth_user_admin, repo["uuid"])
        self._create_artifact(element["uuid"])

        artifacts_url = client.build_collection_uri(self.REPO_ARTIFACTS_PATH)
        with pytest.raises(bazooka_exc.ForbiddenError):
            client.get(artifacts_url)
