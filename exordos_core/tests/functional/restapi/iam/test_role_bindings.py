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
import pytest

from exordos_core.tests.functional.restapi.iam import base
from exordos_core.user_api.iam import constants as iam_c


class TestRoleBindings(base.BaseIamResourceTest):
    def test_create_role_binding_by_admin(self, user_api_client, auth_user_admin):
        client = user_api_client(
            auth_user_admin,
            permissions=[
                iam_c.PERMISSION_USER_CREATE,
            ],
        )
        username = "test_user"
        role_binding_uuid = "15489734-6528-4d0c-ba5b-0f8e2d8b9b5f"
        role_name = "test_role"
        user = client.create_user(
            username=username,
            password="12345678",
        )
        role = client.create_role(name=role_name)

        role_binding = client.create_role_binding(
            user_uuid=user["uuid"],
            role_uuid=role["uuid"],
            uuid=role_binding_uuid,
        )

        assert role_binding["uuid"] == role_binding_uuid

        # cleanup
        client.delete_role_binding(role_binding["uuid"])
        client.delete_role(role["uuid"])
        client.delete_user(user["uuid"])

    def test_create_role_binding_by_user1(
        self, user_api_client, auth_user_admin, auth_test1_user
    ):
        client = user_api_client(
            auth_user_admin,
            permissions=[
                iam_c.PERMISSION_USER_CREATE,
            ],
        )
        username = "test_user"
        role_binding_uuid = "15489734-6528-4d0c-ba5b-0f8e2d8b9b5f"
        role_name = "test_role"
        user = client.create_user(
            username=username,
            password="12345678",
        )
        role = client.create_role(name=role_name)
        user_client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            user_client.create_role_binding(
                user_uuid=user["uuid"],
                role_uuid=role["uuid"],
                uuid=role_binding_uuid,
            )

        # cleanup
        client.delete_role(role["uuid"])
        client.delete_user(user["uuid"])

    def test_list_role_binding_by_admin(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)

        permissions = client.list_role_bindings()

        assert len(permissions) > 0

    def test_create_role_binding_by_user(self, user_api_client, auth_test1_user):
        client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.list_role_bindings()

    def test_get_role_binding_by_admin(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)

        permission = client.get_role_binding(
            uuid="00000000-0000-0000-0000-000000000000"
        )

        assert permission["uuid"] == "00000000-0000-0000-0000-000000000000"

    def test_get_role_binding_by_user(self, user_api_client, auth_test1_user):
        client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.get_role_binding(uuid="00000000-0000-0000-0000-000000000000")

    def test_update_role_binding_by_admin(self, user_api_client, auth_user_admin):
        client = user_api_client(
            auth_user_admin,
            permissions=[
                iam_c.PERMISSION_USER_CREATE,
            ],
        )
        role_binding_uuid = "15489734-6528-4d0c-ba5b-0f8e2d8b9b5f"
        role_name = "test_role"
        user1 = client.create_user(
            username="test_user1",
            password="12345678",
        )
        user2 = client.create_user(
            username="test_user2",
            password="12345678",
        )
        role = client.create_role(name=role_name)
        client.create_role_binding(
            user_uuid=user1["uuid"],
            role_uuid=role["uuid"],
            uuid=role_binding_uuid,
        )

        result = client.update_role_binding(
            uuid=role_binding_uuid,
            user_uuid=user2["uuid"],
        )

        assert result["user"].split("/")[-1] == user2["uuid"]

        # cleanup
        client.delete_role_binding(role_binding_uuid)
        client.delete_role(role["uuid"])
        client.delete_user(user2["uuid"])
        client.delete_user(user1["uuid"])

    def test_update_role_binding_by_user(
        self, user_api_client, auth_user_admin, auth_test1_user
    ):
        client = user_api_client(
            auth_user_admin,
            permissions=[
                iam_c.PERMISSION_USER_CREATE,
            ],
        )
        role_binding_uuid = "15489734-6528-4d0c-ba5b-0f8e2d8b9b5f"
        role_name = "test_role"
        user1 = client.create_user(
            username="test_user1",
            password="12345678",
        )
        user2 = client.create_user(
            username="test_user2",
            password="12345678",
        )
        role = client.create_role(name=role_name)
        client.create_role_binding(
            user_uuid=user1["uuid"],
            role_uuid=role["uuid"],
            uuid=role_binding_uuid,
        )
        user_client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            user_client.update_role_binding(
                uuid=role_binding_uuid,
                user_uuid=user2["uuid"],
            )

        # cleanup
        client.delete_role_binding(role_binding_uuid)
        client.delete_role(role["uuid"])
        client.delete_user(user2["uuid"])
        client.delete_user(user1["uuid"])

    def test_delete_role_binding_by_admin(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)

        result = client.delete_role_binding(uuid="00000000-0000-0000-0000-000000000000")

        assert result is None

    def test_delete_role_binding_by_user(self, user_api_client, auth_test1_user):
        client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.delete_role_binding(uuid="00000000-0000-0000-0000-000000000000")
