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


class TestPermissions(base.BaseIamResourceTest):
    def test_create_permission_by_admin(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)
        permission_name = "iam.test.create"

        permission = client.create_permission(name=permission_name)

        assert permission["name"] == permission_name

    def test_create_permission_by_user1(self, user_api_client, auth_test1_user):
        client = user_api_client(auth_test1_user)
        permission_name = "iam.test.create"

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.create_permission(name=permission_name)

    def test_list_permission_by_admin(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)

        permissions = client.list_permissions()

        assert len(permissions) > 0

    def test_create_permission_by_user(self, user_api_client, auth_test1_user):
        client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.list_permissions()

    def test_get_permission_by_admin(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)

        permission = client.get_permission(uuid="00000000-0000-0000-0000-000000000000")

        assert permission["uuid"] == "00000000-0000-0000-0000-000000000000"

    def test_get_permission_by_user(self, user_api_client, auth_test1_user):
        client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.get_permission(uuid="00000000-0000-0000-0000-000000000000")

    def test_update_permission_by_admin(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)

        permission = client.update_permission(
            uuid="00000000-0000-0000-0000-000000000000", name="iam.test.update"
        )

        assert permission["uuid"] == "00000000-0000-0000-0000-000000000000"

    def test_update_permission_by_user(self, user_api_client, auth_test1_user):
        client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.update_permission(
                uuid="00000000-0000-0000-0000-000000000000",
                name="iam.test.update",
            )

    def test_delete_permission_by_admin(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)
        permission = client.create_permission(name="iam.test.delete")

        result = client.delete_permission(uuid=permission["uuid"])

        assert result is None

    def test_delete_permission_by_user(
        self, user_api_client, auth_user_admin, auth_test1_user
    ):
        admin_client = user_api_client(auth_user_admin)
        permission = admin_client.create_permission(name="test_permission_to_delete")
        client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.delete_permission(uuid=permission["uuid"])

        admin_client.delete_permission(uuid=permission["uuid"])
