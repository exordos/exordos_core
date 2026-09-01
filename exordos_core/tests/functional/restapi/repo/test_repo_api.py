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
from exordos_core.repo.dm import models as repo_models


class TestRepoElements:
    """REST API tests for repo elements endpoints."""

    REPO_ELEMENTS_PATH = ["repo", "elements"]
    REPO_REPOSITORIES_PATH = ["repo", "repositories"]

    def _create_repository(
        self,
        user_api_client,
        auth,
        name="test-repo",
        driver_spec=None,
        project_id=c.EM_PROJECT_ID,
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
                "project_id": str(project_id),
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

    def test_latest_stable_elements_route(self, user_api_client, auth_user_admin):
        repo = self._create_repository(
            user_api_client, auth_user_admin, project_id=c.ZERO_UUID
        )
        element_name = f"element-{sys_uuid.uuid4()}"

        old_element = self._create_element(
            user_api_client,
            auth_user_admin,
            repo["uuid"],
            name=element_name,
            version="1.0.0",
            unique_name=False,
        )
        latest_element = self._create_element(
            user_api_client,
            auth_user_admin,
            repo["uuid"],
            name=element_name,
            version="2.0.0",
            unique_name=False,
        )
        second_repo = self._create_repository(
            user_api_client,
            auth_user_admin,
            driver_spec={"kind": "dummy_migration"},
        )
        second_repo_model = repo_models.Repository.objects.get_one(
            filters={"uuid": dm_filters.EQ(second_repo["uuid"])}
        )
        second_repo_model.priority = 8193
        second_repo_model.save()
        newer_element = self._create_element(
            user_api_client,
            auth_user_admin,
            repo["uuid"],
            name=element_name,
            version="3.0.0",
            unique_name=False,
        )
        newer_element_model = repo_models.RepoElement.objects.get_one(
            filters={"uuid": dm_filters.EQ(newer_element["uuid"])}
        )
        newer_element_model.repository = second_repo_model
        newer_element_model.save()
        prerelease_element = self._create_element(
            user_api_client,
            auth_user_admin,
            repo["uuid"],
            name=element_name,
            version="3.0.0-beta.1",
            unique_name=False,
        )
        other_element = self._create_element(
            user_api_client,
            auth_user_admin,
            repo["uuid"],
            name=f"other-{sys_uuid.uuid4()}",
            version="1.0.0",
            unique_name=False,
        )

        for element, latest, stable in (
            (old_element, False, True),
            (latest_element, True, True),
            (newer_element, True, True),
            (prerelease_element, True, False),
            (other_element, True, True),
        ):
            model = repo_models.RepoElement.objects.get_one(
                filters={"uuid": dm_filters.EQ(element["uuid"])}
            )
            model.latest = latest
            model.stable = stable
            model.save()

        client = user_api_client(auth_user_admin)
        elements_url = client.build_collection_uri(
            ["repo", "store", "latest_stable_elements"]
        )
        elements = client.get(elements_url).json()

        assert {element["uuid"] for element in elements} == {
            newer_element["uuid"],
            other_element["uuid"],
        }
        assert len({element["name"] for element in elements}) == len(elements)

    def test_latest_stable_elements_pagination(self, user_api_client, auth_user_admin):
        repo = self._create_repository(
            user_api_client, auth_user_admin, project_id=c.ZERO_UUID
        )
        elements = [
            self._create_element(
                user_api_client,
                auth_user_admin,
                repo["uuid"],
                version="1.0.0",
            )
            for _ in range(3)
        ]
        for element in elements:
            model = repo_models.RepoElement.objects.get_one(
                filters={"uuid": dm_filters.EQ(element["uuid"])}
            )
            model.latest = True
            model.stable = True
            model.save()

        client = user_api_client(auth_user_admin)
        elements_url = client.build_collection_uri(
            ["repo", "store", "latest_stable_elements"]
        )
        catalogue = client.get(elements_url).json()

        assert {element["uuid"] for element in elements}.issubset(
            {element["uuid"] for element in catalogue}
        )

        first_page = client.get(elements_url, params={"page_limit": 1}).json()
        assert len(first_page) == 1
        assert first_page[0]["uuid"] == catalogue[0]["uuid"]

        next_page = client.get(
            elements_url,
            params={"page_limit": 2, "page_marker": catalogue[0]["uuid"]},
        ).json()
        assert [element["uuid"] for element in next_page] == [
            element["uuid"] for element in catalogue[1:3]
        ]

    def test_store_root_endpoint(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)
        store_url = client.build_collection_uri(["repo", "store"])

        store_routes = client.get(store_url).json()

        assert "elements" in store_routes
        assert "latest_stable_elements" in store_routes

    def test_store_elements_scoped_to_admin_and_own_project(
        self,
        user_api,
        user_api_client,
        auth_user_admin,
        auth_test1_p1_user,
        auth_test2_p1_user,
        user_api_noauth_client,
    ):
        repo = self._create_repository(user_api_client, auth_user_admin)
        element_name = f"element-{sys_uuid.uuid4()}"
        elements = {}
        for label, project_id in (
            ("admin", c.ZERO_UUID),
            ("own", auth_test1_p1_user.project_id),
            ("other", auth_test2_p1_user.project_id),
        ):
            element = self._create_element(
                user_api_client,
                auth_user_admin,
                repo["uuid"],
                name=f"{element_name}-{label}",
                version="1.0.0",
                unique_name=False,
            )
            elements[label] = element
            # project_id is a read-only property, so place the element in
            # its project directly.
            with user_api.engine.session_manager() as session:
                session.execute(
                    "UPDATE repo_elements"
                    " SET project_id = %s, stable = true, latest = true"
                    " WHERE uuid = %s",
                    (
                        sys_uuid.UUID(str(project_id)),
                        sys_uuid.UUID(element["uuid"]),
                    ),
                )

        client = user_api_client(auth_test1_p1_user)
        store_url = client.build_collection_uri(
            ["repo", "store", "latest_stable_elements"]
        )
        visible = {element["uuid"] for element in client.get(store_url).json()}

        assert elements["admin"]["uuid"] in visible
        assert elements["own"]["uuid"] in visible
        assert elements["other"]["uuid"] not in visible

        # A caller without a project, an anonymous request included, reads
        # the shared catalogue only.
        admin_client = user_api_client(auth_user_admin)
        elements_url = admin_client.build_collection_uri(["repo", "store", "elements"])
        for unscoped_client in (admin_client, user_api_noauth_client()):
            visible = {
                element["uuid"] for element in unscoped_client.get(elements_url).json()
            }

            assert elements["admin"]["uuid"] in visible
            assert elements["own"]["uuid"] not in visible
            assert elements["other"]["uuid"] not in visible

    def test_element_stable_versions_action(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)
        repo = self._create_repository(
            user_api_client, auth_user_admin, project_id=c.ZERO_UUID
        )
        other_repo = self._create_repository(
            user_api_client,
            auth_user_admin,
            driver_spec={"kind": "dummy_migration"},
        )
        element_name = f"element-{sys_uuid.uuid4()}"

        stable_element = self._create_element(
            user_api_client,
            auth_user_admin,
            repo["uuid"],
            name=element_name,
            version="1.0.0",
            unique_name=False,
        )
        other_stable_element = self._create_element(
            user_api_client,
            auth_user_admin,
            repo["uuid"],
            name=element_name,
            version="2.0.0",
            unique_name=False,
        )
        prerelease_element = self._create_element(
            user_api_client,
            auth_user_admin,
            repo["uuid"],
            name=element_name,
            version="3.0.0-beta.1",
            unique_name=False,
        )
        other_repo_model = repo_models.Repository.objects.get_one(
            filters={"uuid": dm_filters.EQ(other_repo["uuid"])}
        )
        for element in (other_stable_element, prerelease_element):
            model = repo_models.RepoElement.objects.get_one(
                filters={"uuid": dm_filters.EQ(element["uuid"])}
            )
            model.repository = other_repo_model
            model.save()

        for element, stable in (
            (stable_element, True),
            (other_stable_element, True),
            (prerelease_element, False),
        ):
            model = repo_models.RepoElement.objects.get_one(
                filters={"uuid": dm_filters.EQ(element["uuid"])}
            )
            model.stable = stable
            model.save()

        action_url = client.build_resource_uri(
            [
                "repo/store/elements",
                stable_element["uuid"],
                "actions/stable_versions",
            ]
        )
        elements = client.get(action_url).json()

        assert [element["uuid"] for element in elements] == [
            other_stable_element["uuid"],
        ]

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
