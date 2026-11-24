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
from exordos_core.user_api.iam import constants as c


class TestOrganizationMembers(base.BaseIamResourceTest):
    def _create_organization_member(
        self,
        client,
        organization_uuid,
        user_uuid,
        role=c.OrganizationRole.MEMBER.value,
    ):
        url = client.build_collection_uri(
            ["iam/organizations/", organization_uuid, "members"]
        )
        body = dict(
            organization=f"/v1/iam/organizations/{organization_uuid}",
            user=f"/v1/iam/users/{user_uuid}",
            role=role,
        )

        response = client.post(url, json=body)
        assert response.status_code == 201
        return response.json()

    def _list_organization_members(self, client, organization_uuid):
        url = client.build_collection_uri(
            ["iam/organizations/", organization_uuid, "members"]
        )
        response = client.get(url=url)
        assert response.status_code == 200
        return response.json()

    def _delete_organization_member(
        self, client, organization_uuid, member_uuid
    ) -> None:
        url = client.build_resource_uri(
            ["iam/organizations/", organization_uuid, "members", member_uuid]
        )
        response = client.delete(url)
        assert response.status_code == 204
        return None

    def test_create_member_as_owner_success(
        self,
        user_api_client,
        auth_user_admin,
        auth_test1_user,
    ):
        admin_client = user_api_client(auth_user_admin)
        org = admin_client.create_organization(name="TestOrganization")

        member = self._create_organization_member(
            client=admin_client,
            organization_uuid=org["uuid"],
            user_uuid=auth_test1_user.uuid,
            role=c.OrganizationRole.MEMBER.value,
        )

        assert member is not None
        assert member["user"].endswith(auth_test1_user.uuid)
        assert member["organization"].endswith(org["uuid"])
        assert member["role"] == c.OrganizationRole.MEMBER.value

        # cleanup
        self._delete_organization_member(admin_client, org["uuid"], member["uuid"])
        admin_client.delete_organization(org["uuid"])

    def test_create_member_as_owner_owner_role_success(
        self,
        user_api_client,
        auth_user_admin,
        auth_test1_user,
    ):
        admin_client = user_api_client(auth_user_admin)
        org = admin_client.create_organization(name="TestOrganization")

        member = self._create_organization_member(
            client=admin_client,
            organization_uuid=org["uuid"],
            user_uuid=auth_test1_user.uuid,
            role=c.OrganizationRole.OWNER.value,
        )

        assert member is not None
        assert member["role"] == c.OrganizationRole.OWNER.value

        # cleanup
        self._delete_organization_member(admin_client, org["uuid"], member["uuid"])
        admin_client.delete_organization(org["uuid"])

    def test_list_members_empty_organization(
        self,
        user_api_client,
        auth_user_admin,
    ):
        admin_client = user_api_client(auth_user_admin)
        org = admin_client.create_organization(name="TestOrganization")

        members = self._list_organization_members(
            client=admin_client,
            organization_uuid=org["uuid"],
        )

        assert len(members) == 1  # Owner is automatically added
        assert members[0]["user"].endswith(auth_user_admin.uuid)

        # cleanup
        admin_client.delete_organization(org["uuid"])

    def test_list_members_with_added_member(
        self,
        user_api_client,
        auth_user_admin,
        auth_test1_user,
        auth_test2_user,
    ):
        admin_client = user_api_client(auth_user_admin)
        org = admin_client.create_organization(name="TestOrganization")

        self._create_organization_member(
            client=admin_client,
            organization_uuid=org["uuid"],
            user_uuid=auth_test1_user.uuid,
        )
        self._create_organization_member(
            client=admin_client,
            organization_uuid=org["uuid"],
            user_uuid=auth_test2_user.uuid,
        )

        members = self._list_organization_members(
            client=admin_client,
            organization_uuid=org["uuid"],
        )

        assert len(members) == 3  # Owner + 2 members

        # cleanup
        admin_client.delete_organization(org["uuid"])

    def test_get_member_by_uuid_as_owner_success(
        self,
        user_api_client,
        auth_user_admin,
        auth_test1_user,
    ):
        admin_client = user_api_client(auth_user_admin)
        org = admin_client.create_organization(name="TestOrganization")

        member = self._create_organization_member(
            client=admin_client,
            organization_uuid=org["uuid"],
            user_uuid=auth_test1_user.uuid,
        )

        url = admin_client.build_resource_uri(
            ["iam/organizations/", org["uuid"], "members", member["uuid"]]
        )
        result = admin_client.get(url)
        assert result.status_code == 200
        result = result.json()

        assert result["uuid"] == member["uuid"]
        assert result["user"].endswith(auth_test1_user.uuid)

        # cleanup
        self._delete_organization_member(admin_client, org["uuid"], member["uuid"])
        admin_client.delete_organization(org["uuid"])

    def test_update_member_role_as_owner_success(
        self,
        user_api_client,
        auth_user_admin,
        auth_test1_user,
    ):
        admin_client = user_api_client(auth_user_admin)
        org = admin_client.create_organization(name="TestOrganization")

        member = self._create_organization_member(
            client=admin_client,
            organization_uuid=org["uuid"],
            user_uuid=auth_test1_user.uuid,
            role=c.OrganizationRole.MEMBER.value,
        )

        url = admin_client.build_resource_uri(
            ["iam/organizations/", org["uuid"], "members", member["uuid"]]
        )
        response = admin_client.put(
            url,
            json={"role": c.OrganizationRole.OWNER.value},
        )
        updated = response.json()

        assert updated["uuid"] == member["uuid"]
        assert updated["role"] == c.OrganizationRole.OWNER.value

        # cleanup
        self._delete_organization_member(admin_client, org["uuid"], member["uuid"])
        admin_client.delete_organization(org["uuid"])

    def test_update_member_role_as_non_owner_forbidden(
        self,
        user_api_client,
        auth_user_admin,
        auth_test1_user,
        auth_test2_user,
    ):
        admin_client = user_api_client(auth_user_admin)
        org = admin_client.create_organization(name="TestOrganization")

        member = self._create_organization_member(
            client=admin_client,
            organization_uuid=org["uuid"],
            user_uuid=auth_test1_user.uuid,
            role=c.OrganizationRole.MEMBER.value,
        )

        test1_client = user_api_client(auth_test1_user)
        url = test1_client.build_resource_uri(
            ["iam/organizations/", org["uuid"], "members", member["uuid"]]
        )

        with pytest.raises(bazooka_exc.ForbiddenError):
            test1_client.put(
                url,
                json={"role": c.OrganizationRole.OWNER.value},
            )

        # cleanup
        self._delete_organization_member(admin_client, org["uuid"], member["uuid"])
        admin_client.delete_organization(org["uuid"])

    def test_update_member_role_as_non_owner_with_permission_success(
        self,
        user_api_client,
        auth_user_admin,
        auth_test1_user,
        auth_test2_user,
    ):
        admin_client = user_api_client(auth_user_admin)
        org = admin_client.create_organization(name="TestOrganization")

        member = self._create_organization_member(
            client=admin_client,
            organization_uuid=org["uuid"],
            user_uuid=auth_test1_user.uuid,
            role=c.OrganizationRole.MEMBER.value,
        )

        test1_client = user_api_client(
            auth_test1_user,
            permissions=[c.PERMISSION_ORGANIZATION_WRITE_ALL],
        )
        url = test1_client.build_resource_uri(
            ["iam/organizations/", org["uuid"], "members", member["uuid"]]
        )

        response = test1_client.put(
            url,
            json={"role": c.OrganizationRole.OWNER.value},
        )
        updated = response.json()

        assert updated["role"] == c.OrganizationRole.OWNER.value

        # cleanup
        self._delete_organization_member(admin_client, org["uuid"], member["uuid"])
        admin_client.delete_organization(org["uuid"])

    def test_delete_member_as_owner_success(
        self,
        user_api_client,
        auth_user_admin,
        auth_test1_user,
    ):
        admin_client = user_api_client(auth_user_admin)
        org = admin_client.create_organization(name="TestOrganization")

        member = self._create_organization_member(
            client=admin_client,
            organization_uuid=org["uuid"],
            user_uuid=auth_test1_user.uuid,
        )

        self._delete_organization_member(
            client=admin_client,
            organization_uuid=org["uuid"],
            member_uuid=member["uuid"],
        )

        # Verify member is deleted
        members = self._list_organization_members(
            client=admin_client,
            organization_uuid=org["uuid"],
        )
        assert len(members) == 1  # Only owner remains

        # cleanup
        admin_client.delete_organization(org["uuid"])

    def test_delete_member_as_non_owner_forbidden(
        self,
        user_api_client,
        auth_user_admin,
        auth_test1_user,
        auth_test2_user,
    ):
        admin_client = user_api_client(auth_user_admin)
        org = admin_client.create_organization(name="TestOrganization")

        member = self._create_organization_member(
            client=admin_client,
            organization_uuid=org["uuid"],
            user_uuid=auth_test1_user.uuid,
        )

        test1_client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            test1_client.delete(
                test1_client.build_resource_uri(
                    ["iam/organizations/", org["uuid"], "members", member["uuid"]]
                )
            )

        # cleanup
        admin_client.delete_organization(org["uuid"])

    def test_delete_member_as_non_owner_with_permission_success(
        self,
        user_api_client,
        auth_user_admin,
        auth_test1_user,
        auth_test2_user,
    ):
        admin_client = user_api_client(auth_user_admin)
        org = admin_client.create_organization(name="TestOrganization")

        member = self._create_organization_member(
            client=admin_client,
            organization_uuid=org["uuid"],
            user_uuid=auth_test1_user.uuid,
        )

        test1_client = user_api_client(
            auth_test1_user,
            permissions=[c.PERMISSION_ORGANIZATION_WRITE_ALL],
        )

        test1_client.delete(
            test1_client.build_resource_uri(
                ["iam/organizations/", org["uuid"], "members", member["uuid"]]
            )
        )

        # cleanup
        admin_client.delete_organization(org["uuid"])

    def test_create_member_to_foreign_organization_as_owner_success(
        self,
        user_api_client,
        auth_user_admin,
        auth_test1_user,
        auth_test2_user,
    ):
        test1_client = user_api_client(auth_test1_user)

        org = test1_client.create_organization(name="TestOrganization")

        url = test1_client.build_collection_uri(
            ["iam/organizations/", org["uuid"], "members"]
        )
        body = dict(
            organization=f"/v1/iam/organizations/{org['uuid']}",
            user=f"/v1/iam/users/{auth_test2_user.uuid}",
            role=c.OrganizationRole.MEMBER.value,
        )

        response = test1_client.post(url, json=body)
        assert response.status_code == 201
        member = response.json()

        assert member["user"].endswith(auth_test2_user.uuid)

        # cleanup
        self._delete_organization_member(test1_client, org["uuid"], member["uuid"])
        test1_client.delete_organization(org["uuid"])

    def test_delete_last_owner_member_forbidden(
        self,
        user_api_client,
        auth_user_admin,
        auth_test1_user,
    ):
        admin_client = user_api_client(auth_user_admin)
        org = admin_client.create_organization(name="TestOrganization")

        owner_member = self._create_organization_member(
            client=admin_client,
            organization_uuid=org["uuid"],
            user_uuid=auth_test1_user.uuid,
            role=c.OrganizationRole.OWNER.value,
        )

        # Delete the original owner
        self._delete_organization_member(
            admin_client, org["uuid"], owner_member["uuid"]
        )

        # try to delete the last owner
        with pytest.raises(bazooka_exc.NotFoundError):
            self._delete_organization_member(
                admin_client, org["uuid"], owner_member["uuid"]
            )

        # cleanup
        admin_client.delete_organization(org["uuid"])
