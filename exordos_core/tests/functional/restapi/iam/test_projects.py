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

import uuid as sys_uuid

from bazooka import exceptions as bazooka_exc
import pytest

from exordos_core.common import constants as common_c
from exordos_core.tests.functional.restapi.iam import base
from exordos_core.user_api.iam import constants as c


class TestProjects(base.BaseIamResourceTest):
    def test_create_user_and_check_roles(self, user_api_client, auth_test1_user):
        client = user_api_client(auth_test1_user)
        org = client.create_organization(
            name="TestOrganization",
        )
        project = client.create_project(
            name="TestProject",
            organization_uuid=org["uuid"],
        )

        roles = client.get_user_roles(auth_test1_user.uuid)

        assert self._has_role(roles, common_c.OWNER_ROLE_UUID)

        # cleanup
        client.delete_project(project["uuid"])
        client.delete_organization(org["uuid"])

    def test_list_projects_wo_user_projects(self, user_api_client, auth_test1_user):
        client = user_api_client(auth_test1_user)

        projects = client.list_projects()

        assert len(projects) == 0

    def test_list_projects_one_project(self, user_api_client, auth_test1_p1_user):
        client = user_api_client(auth_test1_p1_user)

        projects = client.list_projects()

        assert len(projects) == 1
        assert projects[0]["name"] == "ProjectU1P1"

    def test_list_projects_invited_user_to_project(
        self,
        user_api_client,
        auth_user_admin,
        auth_test1_p1_user,
        auth_test2_p1_user,
    ):
        admin_client = user_api_client(auth_user_admin)
        client = user_api_client(auth_test1_p1_user)
        u1p1 = client.list_projects()[0]
        role = admin_client.create_role(
            name="testrole",
            description="test role",
            project_id=u1p1["uuid"],
        )
        role_binding = admin_client.bind_role_to_user(
            role["uuid"],
            auth_test2_p1_user.uuid,
            u1p1["uuid"],
        )
        client = user_api_client(auth_test2_p1_user)

        projects = client.list_projects()

        assert len(projects) == 2

        # cleanup
        admin_client.delete_role_binding(role_binding["uuid"])
        admin_client.delete_role(role["uuid"])

    def test_list_projects_admin_user(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)

        projects = client.list_projects()

        assert len(projects) == 4  # 4 projects from migrations

    def test_create_project_wo_project_from_admin(
        self,
        user_api_client,
        auth_user_admin,
    ):
        client = user_api_client(auth_user_admin)
        org = client.create_organization(name="TestOrganization")

        project = client.create_project(
            uuid=str(sys_uuid.uuid4()),
            organization_uuid=org["uuid"],
            name="TestProject",
        )

        assert project["name"] == "TestProject"

        # cleanup
        client.delete_project(project["uuid"])
        client.delete_organization(org["uuid"])

    def test_create_project_wo_project_from_user(
        self,
        user_api_client,
        auth_test1_user,
    ):
        client = user_api_client(auth_test1_user)
        org = client.create_organization(name="TestOrganization")

        project = client.create_project(
            uuid=str(sys_uuid.uuid4()),
            organization_uuid=org["uuid"],
            name="TestProject",
        )

        assert project["name"] == "TestProject"

        # cleanup
        client.delete_project(project["uuid"])
        client.delete_organization(org["uuid"])

    @pytest.mark.sec_issue_10110_997
    def test_create_project_in_foreign_organization_forbidden(
        self,
        user_api_client,
        auth_test1_user,
        auth_test2_user,
    ):
        owner_client = user_api_client(auth_test1_user)
        foreign_client = user_api_client(auth_test2_user)

        org = owner_client.create_organization(name="TestOrganization")

        with pytest.raises(bazooka_exc.ForbiddenError):
            foreign_client.create_project(
                uuid=str(sys_uuid.uuid4()),
                organization_uuid=org["uuid"],
                name="TestProject",
            )

        # cleanup
        owner_client.delete_organization(org["uuid"])

    @pytest.mark.sec_issue_10110_997
    @pytest.mark.parametrize(
        "permissions",
        [
            [c.PERMISSION_PROJECT_WRITE_ALL],
            [c.PERMISSION_ORGANIZATION_WRITE_ALL],
        ],
    )
    def test_create_project_in_foreign_organization_allowed_with_global_perm(
        self,
        user_api_client,
        auth_test1_user,
        auth_test2_user,
        permissions,
    ):
        owner_client = user_api_client(auth_test1_user)
        foreign_client = user_api_client(
            auth_test2_user,
            permissions=permissions,
        )

        org = owner_client.create_organization(name="TestOrganization")

        project = foreign_client.create_project(
            uuid=str(sys_uuid.uuid4()),
            organization_uuid=org["uuid"],
            name="TestProject",
        )

        assert project["name"] == "TestProject"

        # cleanup
        foreign_client.delete_project(project["uuid"])
        owner_client.delete_organization(org["uuid"])

    def test_get_my_project_by_uuid(
        self,
        user_api_client,
        auth_test1_p1_user,
    ):
        client = user_api_client(auth_test1_p1_user)
        u1p1 = client.list_projects()[0]

        project = client.get_project(u1p1["uuid"])

        assert project["uuid"] == u1p1["uuid"]

    def test_get_other_project_by_uuid(
        self,
        user_api_client,
        auth_test1_user,
        auth_test1_p1_user,
    ):
        client = user_api_client(auth_test1_p1_user)
        u1p1 = client.list_projects()[0]
        client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.get_project(u1p1["uuid"])

    def test_get_other_project_by_uuid_for_admin(
        self,
        user_api_client,
        auth_user_admin,
        auth_test1_p1_user,
    ):
        client = user_api_client(auth_test1_p1_user)
        u1p1 = client.list_projects()[0]
        client = user_api_client(auth_user_admin)

        project = client.get_project(u1p1["uuid"])

        assert project["uuid"] == u1p1["uuid"]

    def test_update_my_project_by_uuid(
        self,
        user_api_client,
        auth_test1_p1_user,
    ):
        client = user_api_client(auth_test1_p1_user)
        u1p1 = client.list_projects()[0]

        project = client.update_project(u1p1["uuid"], name="Updated Project Name")

        assert project["uuid"] == u1p1["uuid"]
        assert project["name"] != u1p1["name"]

    def test_update_other_project_by_uuid(
        self,
        user_api_client,
        auth_test1_user,
        auth_test1_p1_user,
    ):
        client = user_api_client(auth_test1_p1_user)
        u1p1 = client.list_projects()[0]
        client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.update_project(u1p1["uuid"], name="Updated Project Name")

    def test_update_other_project_by_uuid_for_admin(
        self,
        user_api_client,
        auth_user_admin,
        auth_test1_p1_user,
    ):
        client = user_api_client(auth_test1_p1_user)
        u1p1 = client.list_projects()[0]
        client = user_api_client(auth_user_admin)

        project = client.update_project(u1p1["uuid"])

        assert project["uuid"] == u1p1["uuid"]

    def test_delete_my_project_by_uuid(
        self,
        user_api_client,
        auth_test1_p1_user,
    ):
        client = user_api_client(auth_test1_p1_user)
        u1p1 = client.list_projects()[0]

        result = client.delete_project(u1p1["uuid"])

        assert result is None

    def test_delete_other_project_by_uuid(
        self,
        user_api_client,
        auth_test1_user,
        auth_test1_p1_user,
    ):
        client = user_api_client(auth_test1_p1_user)
        u1p1 = client.list_projects()[0]
        client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.delete_project(u1p1["uuid"])

    def test_delete_other_project_by_uuid_for_admin(
        self,
        user_api_client,
        auth_user_admin,
        auth_test1_p1_user,
    ):
        client = user_api_client(auth_test1_p1_user)
        u1p1 = client.list_projects()[0]
        client = user_api_client(auth_user_admin)

        result = client.delete_project(u1p1["uuid"])

        assert result is None
