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


class TestRoles(base.BaseIamResourceTest):
    def test_create_role_by_admin(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)
        role_name = "test_role[admin-user]"

        role = client.create_role(name=role_name)

        assert role["name"] == role_name

    def test_create_role_by_user1(self, user_api_client, auth_test1_user):
        client = user_api_client(auth_test1_user)
        role_name = "test_role[test1-user]"

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.create_role(name=role_name)

    def test_list_role_by_admin(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)

        roles = client.list_roles()

        assert len(roles) > 0

    def test_list_role_by_user(self, user_api_client, auth_test1_user):
        client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.list_roles()

    def test_get_role_by_admin(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)

        role = client.get_role(uuid="00000000-0000-0000-0000-000000000000")

        assert role["uuid"] == "00000000-0000-0000-0000-000000000000"

    def test_get_role_by_user(self, user_api_client, auth_test1_user):
        client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.get_role(uuid="00000000-0000-0000-0000-000000000000")

    def test_update_role_by_admin(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)

        role = client.update_role(
            uuid="00000000-0000-0000-0000-000000000000",
            name="updated_test_role",
        )

        assert role["uuid"] == "00000000-0000-0000-0000-000000000000"

    def test_update_role_by_user(self, user_api_client, auth_test1_user):
        client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.update_role(
                uuid="00000000-0000-0000-0000-000000000000",
                name="updated_test_role",
            )

    def test_delete_role_by_admin(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)
        role = client.create_role(name="test_role_to_delete")

        result = client.delete_role(uuid=role["uuid"])

        assert result is None

    def test_delete_role_by_user(
        self, user_api_client, auth_user_admin, auth_test1_user
    ):
        admin_client = user_api_client(auth_user_admin)
        role = admin_client.create_role(name="test_role_to_delete")
        client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.delete_role(uuid=role["uuid"])

        admin_client.delete_role(uuid=role["uuid"])
