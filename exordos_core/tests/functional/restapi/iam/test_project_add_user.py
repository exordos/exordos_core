#    Copyright 2025-2026 Genesis Corporation.
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

from bazooka import exceptions as bazooka_exc
from gcl_iam.tests.functional import clients as iam_clients
import pytest
from restalchemy.dm import filters as dm_filters

from exordos_core.tests.functional.restapi.iam import base
from exordos_core.user_api.iam import constants as iam_c
from exordos_core.user_api.iam.dm import models as iam_models


class TestProjectAddUser(base.BaseIamResourceTest):
    """Tests for POST /v1/iam/projects/<uuid>/actions/add_user/invoke"""

    def _add_user_url(self, client, project_uuid):
        return client.build_resource_uri(
            ["iam/projects", project_uuid, "actions/add_user/invoke"]
        )

    def test_project_owner_adds_user_success(
        self,
        user_api_client,
        auth_user_admin,
        auth_test1_user,
    ):
        """Project owner can add another user to their project."""
        admin_client = user_api_client(auth_user_admin)
        project_client = user_api_client(auth_test1_user)

        # Create org + project + role using admin
        org = admin_client.create_organization(name="TestAddUserOrg")
        project = admin_client.create_project(
            organization_uuid=org["uuid"],
            name="TestAddUserProject",
        )
        role = admin_client.create_role(
            name="test_add_user_role",
            project_id=project["uuid"],
        )
        target_user = admin_client.create_user(
            username="target_user_add",
            password="TestPassword1",
        )
        user_obj = iam_models.User.objects.get_one(
            filters={"uuid": target_user["uuid"]}
        )
        admin_client.confirm_email(
            user_uuid=user_obj.uuid,
            code=str(user_obj.confirmation_code),
        )

        # Make auth_test1_user an owner of the project
        owner_role = admin_client.create_or_get_role("owner")
        admin_client.create_role_binding(
            role_uuid=owner_role["uuid"],
            user_uuid=auth_test1_user.uuid,
            project_id=project["uuid"],
        )

        # Add user to project
        result = project_client.post(
            self._add_user_url(project_client, project["uuid"]),
            json={
                "user_identifier": target_user["uuid"],
                "role_name": "test_add_user_role",
            },
        ).json()

        assert result["user"] == target_user["uuid"]
        assert result["role"] == role["uuid"]
        assert result["project"] == project["uuid"]

        # Verify role binding was created
        bindings = list(
            iam_models.RoleBinding.objects.get_all(
                filters={
                    "user": dm_filters.EQ(target_user["uuid"]),
                    "project": dm_filters.EQ(project["uuid"]),
                    "role": dm_filters.EQ(role["uuid"]),
                }
            )
        )
        assert len(bindings) == 1

        # cleanup
        admin_client.delete_role_binding(bindings[0]["uuid"])
        admin_client.delete_role(role["uuid"])
        admin_client.delete_user(target_user["uuid"])
        admin_client.delete_project(project["uuid"])
        admin_client.delete_organization(org["uuid"])

    def test_user_with_add_user_permission_can_add(
        self,
        user_api_client,
        auth_user_admin,
        auth_test1_user,
    ):
        """User with iam.project.add_user permission can add users."""
        admin_client = user_api_client(auth_user_admin)
        org = admin_client.create_organization(name="PermTestOrg")
        project = admin_client.create_project(
            organization_uuid=org["uuid"],
            name="PermTestProject",
        )
        role = admin_client.create_role(
            name="perm_test_role",
            project_id=project["uuid"],
        )
        target_user = admin_client.create_user(
            username="perm_target_user",
            password="TestPassword2",
        )
        user_obj = iam_models.User.objects.get_one(
            filters={"uuid": target_user["uuid"]}
        )
        admin_client.confirm_email(
            user_uuid=user_obj.uuid,
            code=str(user_obj.confirmation_code),
        )

        # auth_test1_user has add_user permission
        client = user_api_client(
            auth_test1_user,
            permissions=[iam_c.PERMISSION_PROJECT_ADD_USER],
        )

        result = client.post(
            self._add_user_url(client, project["uuid"]),
            json={
                "user_identifier": target_user["uuid"],
                "role_name": "perm_test_role",
            },
        ).json()

        assert result["user"] == target_user["uuid"]

        # cleanup
        admin_client.delete_role_binding(result["uuid"])
        admin_client.delete_role(role["uuid"])
        admin_client.delete_user(target_user["uuid"])
        admin_client.delete_project(project["uuid"])
        admin_client.delete_organization(org["uuid"])

    def test_user_with_write_all_permission_can_add(
        self,
        user_api_client,
        auth_user_admin,
        auth_test1_user,
    ):
        """User with iam.project.write_all permission can add users."""
        admin_client = user_api_client(auth_user_admin)
        org = admin_client.create_organization(name="WriteAllOrg")
        project = admin_client.create_project(
            organization_uuid=org["uuid"],
            name="WriteAllProject",
        )
        role = admin_client.create_role(
            name="write_all_role",
            project_id=project["uuid"],
        )
        target_user = admin_client.create_user(
            username="write_all_target",
            password="TestPassword3",
        )
        user_obj = iam_models.User.objects.get_one(
            filters={"uuid": target_user["uuid"]}
        )
        admin_client.confirm_email(
            user_uuid=user_obj.uuid,
            code=str(user_obj.confirmation_code),
        )

        # auth_test1_user has write_all permission
        client = user_api_client(
            auth_test1_user,
            permissions=[iam_c.PERMISSION_PROJECT_WRITE_ALL],
        )

        result = client.post(
            self._add_user_url(client, project["uuid"]),
            json={
                "user_identifier": target_user["uuid"],
                "role_name": "write_all_role",
            },
        ).json()

        assert result["user"] == target_user["uuid"]

        # cleanup
        admin_client.delete_role_binding(result["uuid"])
        admin_client.delete_role(role["uuid"])
        admin_client.delete_user(target_user["uuid"])
        admin_client.delete_project(project["uuid"])
        admin_client.delete_organization(org["uuid"])

    def test_user_without_permission_cannot_add(
        self,
        user_api_client,
        auth_user_admin,
        auth_test1_user,
        auth_test2_user,
    ):
        """User without project membership or permission gets 403."""
        admin_client = user_api_client(auth_user_admin)
        # Create project owned by auth_test1_user
        org = admin_client.create_organization(name="ForbiddenOrg")
        project = admin_client.create_project(
            organization_uuid=org["uuid"],
            name="ForbiddenProject",
        )

        # auth_test2_user has no access to this project
        client = user_api_client(auth_test2_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.post(
                self._add_user_url(client, project["uuid"]),
                json={
                    "user_identifier": auth_test1_user.uuid,
                    "role_name": "owner",
                },
            )

        # cleanup
        admin_client.delete_project(project["uuid"])
        admin_client.delete_organization(org["uuid"])

    def test_add_user_with_nonexistent_user_fails(
        self,
        user_api_client,
        auth_user_admin,
        auth_test1_p1_user,
    ):
        """Adding a user with non-existent UUID returns 404."""
        client = user_api_client(auth_test1_p1_user)
        u1p1 = client.list_projects()[0]

        with pytest.raises(bazooka_exc.NotFoundError):
            client.post(
                self._add_user_url(client, u1p1["uuid"]),
                json={
                    "user_identifier": "11111111-1111-1111-1111-111111111111",
                    "role_name": "owner",
                },
            )

    def test_add_user_with_nonexistent_role_fails(
        self,
        user_api_client,
        auth_user_admin,
        auth_test1_p1_user,
    ):
        """Adding a user with non-existent role returns 404."""
        client = user_api_client(auth_test1_p1_user)
        u1p1 = client.list_projects()[0]

        with pytest.raises(bazooka_exc.NotFoundError):
            client.post(
                self._add_user_url(client, u1p1["uuid"]),
                json={
                    "user_identifier": auth_test1_p1_user.uuid,
                    "role_name": "nonexistent_role_xyz",
                },
            )

    def test_add_user_owner_role_success(
        self,
        user_api_client,
        auth_user_admin,
        auth_test1_user,
    ):
        """Adding user with 'owner' role works correctly."""
        admin_client = user_api_client(auth_user_admin)
        org = admin_client.create_organization(name="OwnerRoleOrg")
        project = admin_client.create_project(
            organization_uuid=org["uuid"],
            name="OwnerRoleProject",
        )
        target_user = admin_client.create_user(
            username="owner_role_target",
            password="TestPassword4",
        )
        user_obj = iam_models.User.objects.get_one(
            filters={"uuid": target_user["uuid"]}
        )
        admin_client.confirm_email(
            user_uuid=user_obj.uuid,
            code=str(user_obj.confirmation_code),
        )

        # Make auth_test1_user an owner of the project
        owner_role = admin_client.create_or_get_role("owner")
        admin_client.create_role_binding(
            role_uuid=owner_role["uuid"],
            user_uuid=auth_test1_user.uuid,
            project_id=project["uuid"],
        )

        client = user_api_client(auth_test1_user)
        result = client.post(
            self._add_user_url(client, project["uuid"]),
            json={
                "user_identifier": target_user["uuid"],
                "role_name": "owner",
            },
        ).json()

        assert result["user"] == target_user["uuid"]

        # Verify the added user can now list the project
        target_auth = iam_clients.GenesisCoreAuth(
            username=target_user["username"],
            password="TestPassword4",
            client_uuid=admin_client._auth.client_uuid,
            client_id=admin_client._auth.client_id,
            client_secret=admin_client._auth.client_secret,
            uuid=target_user["uuid"],
            email=target_user["email"],
        )
        target_client = user_api_client(target_auth)
        projects = target_client.list_projects()
        project_uuids = [p["uuid"] for p in projects]
        assert project["uuid"] in project_uuids

        # cleanup
        admin_client.delete_user(target_user["uuid"])
        admin_client.delete_project(project["uuid"])
        admin_client.delete_organization(org["uuid"])
