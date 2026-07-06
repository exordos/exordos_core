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

from bazooka import exceptions as bazooka_exc
import pytest

from exordos_core.tests.functional.restapi.iam import base


class TestPermissionBindings(base.BaseIamResourceTest):
    def test_create_permission_binding_by_admin(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)
        permission_name = "iam.test.create"
        permission_binding_uuid = "15489734-6528-4d0c-ba5b-0f8e2d8b9b5f"
        role_name = "test_role"
        permission = client.create_permission(name=permission_name)
        role = client.create_role(name=role_name)

        permission_binding = client.create_permission_binding(
            permission_uuid=permission["uuid"],
            role_uuid=role["uuid"],
            uuid=permission_binding_uuid,
        )

        assert permission_binding["uuid"] == permission_binding_uuid

    def test_create_permission_binding_by_user1(
        self, user_api_client, auth_user_admin, auth_test1_user
    ):
        client = user_api_client(auth_user_admin)
        permission_name = "iam.test.create"
        permission_binding_uuid = "15489734-6528-4d0c-ba5b-0f8e2d8b9b5f"
        role_name = "test_role"
        permission = client.create_permission(name=permission_name)
        role = client.create_role(name=role_name)
        user_client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            user_client.create_permission_binding(
                permission_uuid=permission["uuid"],
                role_uuid=role["uuid"],
                uuid=permission_binding_uuid,
            )

    def test_list_permission_binding_by_admin(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)

        permissions = client.list_permission_bindings()

        assert len(permissions) > 0

    def test_create_permission_binding_by_user(self, user_api_client, auth_test1_user):
        client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.list_permission_bindings()

    def test_get_permission_binding_by_admin(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)

        permission = client.get_permission_binding(
            uuid="00000000-0000-0000-0000-000000000000"
        )

        assert permission["uuid"] == "00000000-0000-0000-0000-000000000000"

    def test_get_permission_binding_by_user(self, user_api_client, auth_test1_user):
        client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.get_permission_binding(uuid="00000000-0000-0000-0000-000000000000")

    def test_update_permission_binding_by_admin(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)
        permission_name = "iam.test.create"
        permission_binding_uuid = "15489734-6528-4d0c-ba5b-0f8e2d8b9b5f"
        role_name = "test_role"
        permission = client.create_permission(name=permission_name)
        role = client.create_role(name=role_name)
        permission_binding = client.create_permission_binding(
            permission_uuid=permission["uuid"],
            role_uuid=role["uuid"],
            uuid=permission_binding_uuid,
        )

        result = client.update_permission_binding(
            uuid=permission_binding["uuid"],
            permission_uuid=permission["uuid"],
        )

        assert result["permission"].split("/")[-1] == permission["uuid"]

    def test_update_permission_binding_by_user(
        self, user_api_client, auth_user_admin, auth_test1_user
    ):
        client = user_api_client(auth_user_admin)
        permission_name = "iam.test.create"
        permission_binding_uuid = "15489734-6528-4d0c-ba5b-0f8e2d8b9b5f"
        role_name = "test_role"
        permission = client.create_permission(name=permission_name)
        role = client.create_role(name=role_name)
        permission_binding = client.create_permission_binding(
            permission_uuid=permission["uuid"],
            role_uuid=role["uuid"],
            uuid=permission_binding_uuid,
        )
        user_client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            user_client.update_permission_binding(
                uuid=permission_binding["uuid"],
                permission_uuid=permission["uuid"],
            )

        client.delete_permission_binding(uuid=permission_binding["uuid"])
        client.delete_role(uuid=role["uuid"])
        client.delete_permission(uuid=permission["uuid"])

    def test_delete_permission_binding_by_admin(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)

        result = client.delete_permission_binding(
            uuid="00000000-0000-0000-0000-000000000000"
        )

        assert result is None

    def test_delete_permission_binding_by_user(self, user_api_client, auth_test1_user):
        client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.delete_permission_binding(
                uuid="00000000-0000-0000-0000-000000000000"
            )
