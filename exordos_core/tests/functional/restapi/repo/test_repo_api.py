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

from exordos_core.common import constants as c


class TestRepoElements:
    """REST API tests for repo elements endpoints."""

    REPO_ELEMENTS_PATH = ["repo", "elements"]
    REPO_REPOSITORIES_PATH = ["repo", "repositories"]

    def _create_repository(
        self, user_api_client, auth, name="test-repo", driver_spec=None
    ):
        """Helper to create a repository with a simple driver spec."""
        client = user_api_client(auth)
        url = client.build_collection_uri(self.REPO_REPOSITORIES_PATH)
        name = f"{name}-{sys_uuid.uuid4()}"
        response = client.post(
            url,
            json={
                "name": name,
                "description": "Test repository",
                "project_id": str(c.SERVICE_PROJECT_ID),
                "sync_mode": "lazy",
                "driver_spec": driver_spec or {"kind": "database"},
            },
        )
        return response.json()

    def _create_element(
        self,
        user_api_client,
        auth,
        repo_uuid,
        name="test-element",
        version="1.0.0",
        unique_name=True,
    ):
        """Helper to create a repo element."""
        client = user_api_client(auth)
        upload_url = client.build_resource_uri(
            ["repo/repositories", repo_uuid, "actions/upload/invoke"]
        )
        if unique_name:
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
        elements_response = client.get(
            elements_url,
            params={"repository": repo_uuid},
        )
        elements = elements_response.json()
        return next(
            element
            for element in elements
            if element["name"] == name and element["version"] == version
        )

    # ------------------------------------------------------------------
    # GET / FILTER tests
    # ------------------------------------------------------------------

    def test_get_element_by_admin(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)

        repo = self._create_repository(user_api_client, auth_user_admin)
        element = self._create_element(user_api_client, auth_user_admin, repo["uuid"])

        element_url = client.build_resource_uri(["repo/elements", element["uuid"]])
        result = client.get(element_url).json()
        assert result["uuid"] == element["uuid"]
        assert result["name"] == element["name"]
        assert result["version"] == element["version"]

    def test_get_element_by_user(
        self, user_api_client, auth_test1_user, auth_user_admin
    ):
        client = user_api_client(auth_test1_user)

        repo = self._create_repository(user_api_client, auth_user_admin)
        element = self._create_element(user_api_client, auth_user_admin, repo["uuid"])

        element_url = client.build_resource_uri(["repo/elements", element["uuid"]])
        with pytest.raises(bazooka_exc.ForbiddenError):
            client.get(element_url)

    def test_get_nonexistent_element(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)
        fake_uuid = sys_uuid.uuid4()
        element_url = client.build_resource_uri(["repo/elements", fake_uuid])

        with pytest.raises(bazooka_exc.NotFoundError):
            client.get(element_url)

    def test_list_elements(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)

        repo = self._create_repository(user_api_client, auth_user_admin)
        self._create_element(
            user_api_client, auth_user_admin, repo["uuid"], name="elem-1"
        )
        self._create_element(
            user_api_client, auth_user_admin, repo["uuid"], name="elem-2"
        )

        elements_url = client.build_collection_uri(self.REPO_ELEMENTS_PATH)
        elements = client.get(elements_url).json()
        repo_elements = [
            e for e in elements if e.get("repository", "").endswith(repo["uuid"])
        ]
        assert len(repo_elements) >= 2

    def test_filter_elements_by_repository(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)

        repo1 = self._create_repository(user_api_client, auth_user_admin, name="repo-a")
        repo2 = self._create_repository(
            user_api_client,
            auth_user_admin,
            name="repo-b",
            driver_spec={"kind": "bootstrap", "manifests_dir": "/tmp/repo-b"},
        )

        self._create_element(
            user_api_client, auth_user_admin, repo1["uuid"], name="elem-in-repo1"
        )
        elements_url = client.build_collection_uri(self.REPO_ELEMENTS_PATH)
        elements = client.get(elements_url, params={"repository": repo1["uuid"]}).json()
        assert len(elements) == 1
        assert elements[0]["repository"].endswith(repo1["uuid"])
        assert not elements[0]["repository"].endswith(repo2["uuid"])

    def test_filter_stable(self, user_api_client, auth_user_admin):
        """stable=true should return only stable versions."""
        client = user_api_client(auth_user_admin)
        repo = self._create_repository(user_api_client, auth_user_admin)

        # Create stable and pre-release elements
        stable_element = self._create_element(
            user_api_client,
            auth_user_admin,
            repo["uuid"],
            name="stable-elem",
            version="1.0.0",
        )
        self._create_element(
            user_api_client,
            auth_user_admin,
            repo["uuid"],
            name="beta-elem",
            version="2.0.0-beta.1",
        )
        self._create_element(
            user_api_client,
            auth_user_admin,
            repo["uuid"],
            name="rc-elem",
            version="1.5.0-rc.2",
        )
        self._create_element(
            user_api_client,
            auth_user_admin,
            repo["uuid"],
            name="alpha-elem",
            version="3.0.0-alpha",
        )
        stable_element_2 = self._create_element(
            user_api_client,
            auth_user_admin,
            repo["uuid"],
            name="stable-elem-2",
            version="2.0.0",
        )

        # Filter stable only
        elements_url = client.build_collection_uri(self.REPO_ELEMENTS_PATH)
        elements = client.get(
            elements_url,
            params={"stable": "true", "repository": repo["uuid"]},
        ).json()

        # Should only contain stable versions
        for elem in elements:
            assert elem["version"].count("-") == 0, (
                f"Element {elem['name']} has pre-release version {elem['version']}"
            )

        # Should have at least the 2 stable versions we created
        names = {e["name"] for e in elements}
        assert stable_element["name"] in names
        assert stable_element_2["name"] in names

    def test_filter_stable_false_returns_all(self, user_api_client, auth_user_admin):
        """stable=false should return all versions including pre-releases."""
        client = user_api_client(auth_user_admin)
        repo = self._create_repository(user_api_client, auth_user_admin)

        self._create_element(
            user_api_client,
            auth_user_admin,
            repo["uuid"],
            name="stable-elem",
            version="1.0.0",
        )
        self._create_element(
            user_api_client,
            auth_user_admin,
            repo["uuid"],
            name="beta-elem",
            version="2.0.0-beta.1",
        )

        # With stable=false
        elements_url = client.build_collection_uri(self.REPO_ELEMENTS_PATH)
        elements = client.get(
            elements_url,
            params={"stable": "false", "repository": repo["uuid"]},
        ).json()

        repo_elements = [
            e for e in elements if e.get("repository", "").endswith(repo["uuid"])
        ]
        assert len(repo_elements) == 2

    def test_filter_stable_default_returns_all(self, user_api_client, auth_user_admin):
        """Without stable parameter, all versions should be returned."""
        client = user_api_client(auth_user_admin)
        repo = self._create_repository(user_api_client, auth_user_admin)

        self._create_element(
            user_api_client,
            auth_user_admin,
            repo["uuid"],
            name="stable-elem",
            version="1.0.0",
        )
        self._create_element(
            user_api_client,
            auth_user_admin,
            repo["uuid"],
            name="beta-elem",
            version="2.0.0-beta.1",
        )

        # Without stable parameter
        elements_url = client.build_collection_uri(self.REPO_ELEMENTS_PATH)
        elements = client.get(elements_url, params={"repository": repo["uuid"]}).json()

        repo_elements = [
            e for e in elements if e.get("repository", "").endswith(repo["uuid"])
        ]
        assert len(repo_elements) == 2

    def test_filter_latest(self, user_api_client, auth_user_admin):
        """latest=true should return only the latest version per element name."""
        client = user_api_client(auth_user_admin)
        repo = self._create_repository(user_api_client, auth_user_admin)

        # Create multiple versions of the same element
        self._create_element(
            user_api_client,
            auth_user_admin,
            repo["uuid"],
            name="my-elem",
            version="0.9.0",
            unique_name=False,
        )
        self._create_element(
            user_api_client,
            auth_user_admin,
            repo["uuid"],
            name="my-elem",
            version="1.0.0",
            unique_name=False,
        )
        self._create_element(
            user_api_client,
            auth_user_admin,
            repo["uuid"],
            name="my-elem",
            version="2.0.0",
            unique_name=False,
        )
        # Different element - should also be returned once
        self._create_element(
            user_api_client,
            auth_user_admin,
            repo["uuid"],
            name="other-elem",
            version="1.0.0",
            unique_name=False,
        )

        elements_url = client.build_collection_uri(self.REPO_ELEMENTS_PATH)
        elements = client.get(
            elements_url,
            params={"latest": "true", "repository": repo["uuid"]},
        ).json()

        repo_elements = [
            e for e in elements if e.get("repository", "").endswith(repo["uuid"])
        ]

        # Should have exactly 2 elements (one per unique name)
        assert len(repo_elements) == 2

        # Check that the latest versions are returned
        by_name = {e["name"]: e for e in repo_elements}
        assert by_name["my-elem"]["version"] == "2.0.0"
        assert by_name["other-elem"]["version"] == "1.0.0"

    def test_filter_latest_with_stable(self, user_api_client, auth_user_admin):
        """latest=true combined with stable=true should return latest stable per name."""
        client = user_api_client(auth_user_admin)
        repo = self._create_repository(user_api_client, auth_user_admin)

        self._create_element(
            user_api_client,
            auth_user_admin,
            repo["uuid"],
            name="my-elem",
            version="1.0.0",
            unique_name=False,
        )
        self._create_element(
            user_api_client,
            auth_user_admin,
            repo["uuid"],
            name="my-elem",
            version="2.0.0-beta.1",
            unique_name=False,
        )
        self._create_element(
            user_api_client,
            auth_user_admin,
            repo["uuid"],
            name="my-elem",
            version="1.5.0",
            unique_name=False,
        )

        elements_url = client.build_collection_uri(self.REPO_ELEMENTS_PATH)
        elements = client.get(
            elements_url,
            params={"latest": "true", "stable": "true", "repository": repo["uuid"]},
        ).json()

        repo_elements = [
            e for e in elements if e.get("repository", "").endswith(repo["uuid"])
        ]

        assert len(repo_elements) == 1
        assert repo_elements[0]["version"] == "1.5.0"

    # ------------------------------------------------------------------
    # DELETE tests
    # ------------------------------------------------------------------

    def test_delete_element_by_admin(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)

        repo = self._create_repository(user_api_client, auth_user_admin)
        element = self._create_element(user_api_client, auth_user_admin, repo["uuid"])

        element_url = client.build_resource_uri(["repo/elements", element["uuid"]])
        result = client.delete(element_url)
        assert result.status_code == 204

        with pytest.raises(bazooka_exc.NotFoundError):
            client.get(element_url)

    def test_delete_element_by_user(
        self, user_api_client, auth_test1_user, auth_user_admin
    ):
        client = user_api_client(auth_test1_user)

        repo = self._create_repository(user_api_client, auth_user_admin)
        element = self._create_element(user_api_client, auth_user_admin, repo["uuid"])

        element_url = client.build_resource_uri(["repo/elements", element["uuid"]])
        with pytest.raises(bazooka_exc.ForbiddenError):
            client.delete(element_url)

    def test_delete_installed_element_raises_error(
        self, user_api_client, auth_user_admin
    ):
        client = user_api_client(auth_user_admin)

        repo = self._create_repository(user_api_client, auth_user_admin)
        element = self._create_element(user_api_client, auth_user_admin, repo["uuid"])

        # Install the element first
        element_url = client.build_resource_uri(["repo/elements", element["uuid"]])
        client.post(f"{element_url}/actions/install/invoke")

        with pytest.raises(bazooka_exc.BadRequestError):
            client.delete(element_url)

    # ------------------------------------------------------------------
    # INSTALL / UNINSTALL action tests
    # ------------------------------------------------------------------

    def test_install_element(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)

        repo = self._create_repository(user_api_client, auth_user_admin)
        element = self._create_element(user_api_client, auth_user_admin, repo["uuid"])

        element_url = client.build_resource_uri(["repo/elements", element["uuid"]])
        result = client.post(f"{element_url}/actions/install/invoke").json()
        assert result["uuid"] == element["uuid"]
        assert result["name"] == element["name"]

    def test_install_element_by_user(
        self, user_api_client, auth_test1_user, auth_user_admin
    ):
        client = user_api_client(auth_test1_user)

        repo = self._create_repository(user_api_client, auth_user_admin)
        element = self._create_element(user_api_client, auth_user_admin, repo["uuid"])

        element_url = client.build_resource_uri(["repo/elements", element["uuid"]])
        with pytest.raises(bazooka_exc.ForbiddenError):
            client.post(f"{element_url}/actions/install/invoke")

    def test_uninstall_element(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)

        repo = self._create_repository(user_api_client, auth_user_admin)
        element = self._create_element(user_api_client, auth_user_admin, repo["uuid"])

        element_url = client.build_resource_uri(["repo/elements", element["uuid"]])
        # Install first
        client.post(f"{element_url}/actions/install/invoke")

        # Uninstall
        result = client.post(f"{element_url}/actions/uninstall/invoke").json()
        assert result["uuid"] == element["uuid"]
        assert result["name"] == element["name"]

    def test_uninstall_element_by_user(
        self, user_api_client, auth_test1_user, auth_user_admin
    ):
        client = user_api_client(auth_test1_user)

        repo = self._create_repository(user_api_client, auth_user_admin)
        element = self._create_element(user_api_client, auth_user_admin, repo["uuid"])

        element_url = client.build_resource_uri(["repo/elements", element["uuid"]])
        with pytest.raises(bazooka_exc.ForbiddenError):
            client.post(f"{element_url}/actions/uninstall/invoke")

    # ------------------------------------------------------------------
    # UPGRADE action tests
    # ------------------------------------------------------------------

    def test_upgrade_element(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)

        repo = self._create_repository(user_api_client, auth_user_admin)
        name = f"upgrade-element-{sys_uuid.uuid4()}"
        element = self._create_element(
            user_api_client,
            auth_user_admin,
            repo["uuid"],
            name=name,
            version="1.0.0",
            unique_name=False,
        )
        target_element = self._create_element(
            user_api_client,
            auth_user_admin,
            repo["uuid"],
            name=name,
            version="2.0.0",
            unique_name=False,
        )

        element_url = client.build_resource_uri(["repo/elements", element["uuid"]])
        client.post(f"{element_url}/actions/install/invoke")
        with pytest.raises(bazooka_exc.BadRequestError):
            client.post(
                f"{element_url}/actions/upgrade/invoke",
                json={"target": target_element["uuid"]},
            )

    def test_upgrade_element_by_user(
        self, user_api_client, auth_test1_user, auth_user_admin
    ):
        client = user_api_client(auth_test1_user)

        repo = self._create_repository(user_api_client, auth_user_admin)
        element = self._create_element(user_api_client, auth_user_admin, repo["uuid"])

        element_url = client.build_resource_uri(["repo/elements", element["uuid"]])
        with pytest.raises(bazooka_exc.ForbiddenError):
            client.post(
                f"{element_url}/actions/upgrade/invoke",
                json={"target": "2.0.0"},
            )

    # ------------------------------------------------------------------
    # EDIT action tests
    # ------------------------------------------------------------------

    def test_edit_element_manifest(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)

        repo = self._create_repository(user_api_client, auth_user_admin)
        element = self._create_element(user_api_client, auth_user_admin, repo["uuid"])

        new_manifest = {
            "name": element["name"],
            "version": element["version"],
            "resources": {},
            "description": "Updated description",
        }
        element_url = client.build_resource_uri(["repo/elements", element["uuid"]])
        result = client.post(
            f"{element_url}/actions/edit/invoke",
            json={"manifest": new_manifest},
        ).json()
        assert result["uuid"] == element["uuid"]
        assert result["manifest"]["description"] == "Updated description"

    def test_edit_element_by_user(
        self, user_api_client, auth_test1_user, auth_user_admin
    ):
        client = user_api_client(auth_test1_user)

        repo = self._create_repository(user_api_client, auth_user_admin)
        element = self._create_element(user_api_client, auth_user_admin, repo["uuid"])

        element_url = client.build_resource_uri(["repo/elements", element["uuid"]])
        with pytest.raises(bazooka_exc.ForbiddenError):
            client.post(
                f"{element_url}/actions/edit/invoke",
                json={"manifest": {"name": "test-element", "version": "1.0.0"}},
            )

    # ------------------------------------------------------------------
    # Hidden fields tests
    # ------------------------------------------------------------------

    def test_hidden_field_installation_state(self, user_api_client, auth_user_admin):
        """installation_state should be hidden from GET responses."""
        client = user_api_client(auth_user_admin)

        repo = self._create_repository(user_api_client, auth_user_admin)
        element = self._create_element(user_api_client, auth_user_admin, repo["uuid"])

        element_url = client.build_resource_uri(["repo/elements", element["uuid"]])
        result = client.get(element_url)
        assert "installation_state" not in result
