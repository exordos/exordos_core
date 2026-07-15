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


class TestOrganizations(base.BaseIamResourceTest):
    def test_create_organization_wo_auth_unauthorized(self, user_api_noauth_client):
        client = user_api_noauth_client()

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.create_organization(name="TestOrganization")

    def _list_organization_members(self, client, organization_uuid):
        url = client.build_collection_uri(
            ["iam/organizations/", organization_uuid, "members"]
        )
        response = client.get(url=url)
        assert response.status_code == 200
        return response.json()

    def _create_organization(self, client, user, info=None):
        org = client.create_organization(
            name="TestOrganization",
            info=info or {},
        )
        members = self._list_organization_members(client, org["uuid"])
        return org, members

    def test_create_organization_admin_auth_success(
        self, user_api_client, auth_user_admin
    ):
        client = user_api_client(auth_user_admin)
        org_info = {"some": "info"}

        org, members = self._create_organization(
            client=client,
            user=auth_user_admin,
            info=org_info,
        )

        assert org is not None
        assert org["info"] == org_info
        assert len(members) == 1
        assert members[0]["role"] == c.OrganizationRole.OWNER.value

        # cleanup
        client.delete_organization(org["uuid"])

    def test_create_organization_test1_auth_success(
        self, user_api_client, auth_test1_user
    ):
        client = user_api_client(
            auth_test1_user,
        )

        org, members = self._create_organization(client, auth_test1_user)

        assert org is not None
        assert len(members) == 1
        assert members[0]["role"] == c.OrganizationRole.OWNER.value

        # cleanup
        client.delete_organization(org["uuid"])

    def test_list_organizations_admin_auth_success(
        self, user_api_client, auth_user_admin
    ):
        client = user_api_client(auth_user_admin)

        result = client.list_organizations()

        assert isinstance(result, list)

    def test_list_organizations_test1_auth_empty(
        self, user_api_client, auth_test1_user
    ):
        client = user_api_client(auth_test1_user)

        orgs = client.list_organizations()

        assert isinstance(orgs, list)
        assert len(orgs) == 0

    def test_list_organizations_test1_auth_success(
        self, user_api_client, auth_user_admin, auth_test1_user
    ):
        admin_client = user_api_client(auth_user_admin)
        test_user_client = user_api_client(auth_test1_user)
        org1 = admin_client.create_organization(name="TestOrganization1")
        org2 = admin_client.create_organization(name="TestOrganization2")
        org3 = test_user_client.create_organization(name="TestOrganization3")
        org4 = test_user_client.create_organization(name="TestOrganization4")

        orgs = test_user_client.list_organizations()

        assert isinstance(orgs, list)
        assert len(orgs) == 2

        # cleanup
        for org in [org4, org3, org2, org1]:
            try:
                admin_client.delete_organization(org["uuid"])
            except Exception:
                pass

    def test_list_all_organizations_test1_auth_success(
        self, user_api_client, auth_user_admin, auth_test1_user
    ):
        admin_client = user_api_client(auth_user_admin)
        test_user_client = user_api_client(
            auth_test1_user,
            permissions=[
                c.PERMISSION_ORGANIZATION_READ_ALL,
            ],
        )
        org1 = admin_client.create_organization(name="TestOrganization1")
        org2 = admin_client.create_organization(name="TestOrganization2")
        org3 = test_user_client.create_organization(name="TestOrganization3")
        org4 = test_user_client.create_organization(name="TestOrganization4")

        orgs = test_user_client.list_organizations()

        assert isinstance(orgs, list)
        assert len(orgs) == 6  # 2 for admin, 2 for test user and 2 default

        # cleanup
        for org in [org4, org3, org2, org1]:
            try:
                admin_client.delete_organization(org["uuid"])
            except Exception:
                pass

    def test_get_any_organization_test1_auth_success(
        self, user_api_client, auth_user_admin, auth_test1_user
    ):
        admin_client = user_api_client(auth_user_admin)
        test_user_client = user_api_client(
            auth_test1_user,
        )
        org1 = admin_client.create_organization(name="TestOrganization1")
        org2 = test_user_client.create_organization(name="TestOrganization2")

        org1_result = test_user_client.get_organization(org1["uuid"])
        org2_result = test_user_client.get_organization(org2["uuid"])

        assert org1_result["uuid"] == org1["uuid"]
        assert org2_result["uuid"] == org2["uuid"]

        # cleanup
        admin_client.delete_organization(org1["uuid"])
        test_user_client.delete_organization(org2["uuid"])

    def test_update_organization_test1_auth_success(
        self, user_api_client, auth_test1_user
    ):
        new_name = "blablabla"
        test_user_client = user_api_client(
            auth_test1_user,
        )
        org1 = test_user_client.create_organization(name="TestOrganization1")

        org1_result = test_user_client.update_organization(
            uuid=org1["uuid"], name=new_name
        )

        assert org1_result["uuid"] == org1["uuid"]
        assert org1_result["name"] == new_name

        # cleanup
        test_user_client.delete_organization(org1["uuid"])

    def test_update_other_organization_test1_auth_forbidden(
        self, user_api_client, auth_user_admin, auth_test1_user
    ):
        new_name = "blablabla"
        admin_client = user_api_client(auth_user_admin)
        test_user_client = user_api_client(
            auth_test1_user,
        )
        org1 = admin_client.create_organization(name="TestOrganization1")

        with pytest.raises(bazooka_exc.ForbiddenError):
            test_user_client.update_organization(uuid=org1["uuid"], name=new_name)

        # cleanup
        admin_client.delete_organization(org1["uuid"])

    def test_update_other_organization_test1_auth_success(
        self, user_api_client, auth_user_admin, auth_test1_user
    ):
        new_name = "blablabla"
        admin_client = user_api_client(auth_user_admin)
        test_user_client = user_api_client(
            auth_test1_user,
            permissions=[
                c.PERMISSION_ORGANIZATION_WRITE_ALL,
            ],
        )
        org1 = admin_client.create_organization(name="TestOrganization1")

        org1_result = test_user_client.update_organization(
            uuid=org1["uuid"], name=new_name
        )

        assert org1_result["uuid"] == org1["uuid"]
        assert org1_result["name"] == new_name

        # cleanup
        admin_client.delete_organization(org1["uuid"])

    def test_delete_my_organization_test1_auth_access(
        self, user_api_client, auth_test1_user
    ):
        test_user_client = user_api_client(
            auth_test1_user,
        )
        org1 = test_user_client.create_organization(name="TestOrganization1")

        result = test_user_client.delete_organization(org1["uuid"])

        assert result is None

    def test_delete_any_organization_test1_auth_access(
        self, user_api_client, auth_user_admin, auth_test1_user
    ):
        admin_client = user_api_client(auth_user_admin)
        test_user_client = user_api_client(
            auth_test1_user,
            permissions=[
                c.PERMISSION_ORGANIZATION_DELETE_ALL,
            ],
        )
        org1 = admin_client.create_organization(name="TestOrganization1")

        result = test_user_client.delete_organization(org1["uuid"])

        assert result is None
