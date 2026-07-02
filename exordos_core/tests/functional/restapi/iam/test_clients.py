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
from gcl_iam.tests.functional import clients as iam_clients
import pytest

from exordos_core.common import constants as common_c
from exordos_core.tests.functional.restapi.iam import base
from exordos_core.user_api.iam import constants as iam_c
from exordos_core.user_api.iam.dm import models as iam_models

TEST_PROJECT_ID = str(sys_uuid.uuid4())


class TestClients(base.BaseIamResourceTest):
    def test_create_iam_client_by_admin(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)
        iam_client_name = "test_client[admin-user]"

        signature_algorithm = {
            "kind": "HS256",
            "secret_uuid": "00000000-0000-0000-0000-000000000001",
            "previous_secret_uuid": None,
        }

        iam_client = client.create_iam_client(
            name=iam_client_name,
            client_id="client_id",
            secret="12345678",
            signature_algorithm=signature_algorithm,
        )

        assert iam_client["name"] == iam_client_name

        # cleanup
        client.delete_iam_client(iam_client["uuid"])

    def test_create_iam_client_by_user1(self, user_api_client, auth_test1_user):
        client = user_api_client(auth_test1_user)
        iam_client_name = "test_client[admin-user]"

        signature_algorithm = {
            "kind": "HS256",
            "secret_uuid": "00000000-0000-0000-0000-000000000001",
            "previous_secret_uuid": None,
        }

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.create_iam_client(
                name=iam_client_name,
                client_id="client_id",
                secret="12345678",
                signature_algorithm=signature_algorithm,
            )

    def test_list_iam_clients_by_admin(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)

        iam_clients = client.list_iam_clients()

        assert len(iam_clients) > 0

    def test_list_iam_clients_by_user(self, user_api_client, auth_test1_user):
        client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.list_iam_clients()

    def test_get_iam_clients_by_admin(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)
        iam_client_uuid = "00000000-0000-0000-0000-000000000000"

        iam_client = client.get_iam_client(uuid=iam_client_uuid)

        assert iam_client["uuid"] == iam_client_uuid

    def test_get_iam_clients_by_user(self, user_api_client, auth_test1_user):
        client = user_api_client(auth_test1_user)
        iam_client_uuid = "00000000-0000-0000-0000-000000000000"

        iam_client = client.get_iam_client(uuid=iam_client_uuid)

        assert iam_client["uuid"] == iam_client_uuid

    def test_update_iam_clients_by_admin(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)
        iam_client_uuid = "00000000-0000-0000-0000-000000000000"
        new_name = "new_name"

        result = client.update_iam_client(
            uuid=iam_client_uuid,
            name=new_name,
        )

        assert result["name"] == new_name

    def test_update_iam_clients_by_user(self, user_api_client, auth_test1_user):
        client = user_api_client(auth_test1_user)
        iam_client_uuid = "00000000-0000-0000-0000-000000000000"
        new_name = "new_name"

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.update_iam_client(
                uuid=iam_client_uuid,
                name=new_name,
            )

    def test_delete_iam_clients_by_admin(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)
        iam_client_uuid = "00000000-0000-0000-0000-000000000000"

        iam_client = client.delete_iam_client(uuid=iam_client_uuid)

        assert iam_client is None

    def test_delete_iam_clients_by_user(self, user_api_client, auth_test1_user):
        client = user_api_client(auth_test1_user)
        iam_client_uuid = "00000000-0000-0000-0000-000000000000"

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.delete_iam_client(uuid=iam_client_uuid)

    def test_me_wo_organization_success(self, user_api_client, auth_test1_user):
        client = user_api_client(auth_test1_user)

        result = client.me()

        assert result["user"]["uuid"] == auth_test1_user.uuid
        assert result["organization"] == []
        assert result["project_id"] is None

    def test_me_with_organization_success(self, user_api_client, auth_test1_user):
        client = user_api_client(
            auth_test1_user,
        )
        org1 = client.create_organization("OrganizationName1")
        org2 = client.create_organization("OrganizationName2")

        result = client.me()

        assert result["user"]["uuid"] == auth_test1_user.uuid
        assert len(result["organization"]) == 2
        assert result["project_id"] is None

        # cleanup
        for org in [org2, org1]:
            client.delete_organization(org["uuid"])

    def test_me_with_organization_and_project_success(
        self, user_api_client, auth_test1_p1_user
    ):
        client = user_api_client(auth_test1_p1_user)

        result = client.me()

        assert result["user"]["uuid"] == auth_test1_p1_user.uuid
        assert len(result["organization"]) == 1
        assert result["project_id"] == auth_test1_p1_user.project_id

    def test_http_code_with_invalid_token(self, user_api_noauth_client):
        client = user_api_noauth_client()
        url = client.build_collection_uri(["iam/roles"])

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.get(url)

    def test_token_ttl_success(self, user_api_client, auth_test1_user):
        client = user_api_client(auth_test1_user)
        token_params = auth_test1_user.get_password_auth_params()
        token_params["ttl"] = 1.0
        token_params["refresh_ttl"] = 2.0

        token_info = client.post(
            url=auth_test1_user.get_token_url(endpoint=client.endpoint),
            data=token_params,
        ).json()

        assert token_info["refresh_expires_in"] == 1

    def test_logout_deletes_token_from_db(self, user_api_client, auth_test1_user):
        client = user_api_client(auth_test1_user)
        me = client.me()

        assert me["user"]["uuid"] == auth_test1_user.uuid

        logout_url = client.build_resource_uri(
            [
                "iam/clients",
                auth_test1_user.client_uuid,
                "actions",
                "logout",
                "invoke",
            ]
        )
        client.post(url=logout_url)

        with pytest.raises(bazooka_exc.UnauthorizedError):
            client.me()

    def test_get_token_with_invalid_client_id_error(
        self, user_api_noauth_client, auth_test1_user
    ):
        client = user_api_noauth_client()
        token_params = auth_test1_user.get_password_auth_params()
        token_params["client_id"] = "wrong-client-id"

        with pytest.raises(bazooka_exc.UnauthorizedError):
            client.post(
                url=auth_test1_user.get_token_url(endpoint=client.endpoint),
                data=token_params,
            )

    def test_get_token_with_invalid_client_secret_error(
        self, user_api_noauth_client, auth_test1_user
    ):
        client = user_api_noauth_client()
        token_params = auth_test1_user.get_password_auth_params()
        token_params["client_secret"] = "wrong-client-secret"

        with pytest.raises(bazooka_exc.UnauthorizedError):
            client.post(
                url=auth_test1_user.get_token_url(endpoint=client.endpoint),
                data=token_params,
            )

    def test_get_token_invalid_credentials_no_user_and_wrong_password_same_error(
        self, user_api_client, auth_test1_user
    ):
        client = user_api_client(auth_test1_user)
        url = auth_test1_user.get_token_url(endpoint=client.endpoint)

        no_user_params = auth_test1_user.get_password_auth_params()
        no_user_params["username"] = "user_does_not_exist"
        no_user_params["password"] = "obviously-wrong-password"

        wrong_password_params = auth_test1_user.get_password_auth_params()
        wrong_password_params["password"] = "obviously-wrong-password"

        with pytest.raises(bazooka_exc.BadRequestError) as no_user_exc:
            client.post(url=url, data=no_user_params)
        with pytest.raises(bazooka_exc.BadRequestError) as wrong_password_exc:
            client.post(url=url, data=wrong_password_params)

        assert type(no_user_exc.value) is type(wrong_password_exc.value)
        assert not isinstance(no_user_exc.value, bazooka_exc.NotFoundError)
        assert not isinstance(wrong_password_exc.value, bazooka_exc.NotFoundError)

    @pytest.fixture(
        scope="function",
        params=[
            ("project",),
            ("project:default",),
            (f"project:{TEST_PROJECT_ID}",),
        ],
    )
    def scope_test(self, request):
        return request.param[0]

    def test_get_no_scoped_token_success(
        self, user_api_client, auth_test1_user, decode_id_token
    ):
        client = user_api_client(auth_test1_user)
        token_params = auth_test1_user.get_password_auth_params()

        token_info = client.post(
            url=auth_test1_user.get_token_url(endpoint=client.endpoint),
            data=token_params,
        ).json()

        id_token = decode_id_token(token_info["id_token"])
        assert token_info["scope"] == ""
        assert id_token["project_id"] is None

    def test_get_empty_scoped_token_success(
        self, user_api_client, auth_test1_user, decode_id_token
    ):
        client = user_api_client(auth_test1_user)
        token_params = auth_test1_user.get_password_auth_params()
        token_params["scope"] = ""

        token_info = client.post(
            url=auth_test1_user.get_token_url(endpoint=client.endpoint),
            data=token_params,
        ).json()

        id_token = decode_id_token(token_info["id_token"])
        assert token_info["scope"] == ""
        assert id_token["project_id"] is None

    def test_get_scoped_token_no_project_no_organization_success(
        self, user_api_client, auth_test1_user, decode_id_token, scope_test
    ):
        client = user_api_client(auth_test1_user)
        token_params = auth_test1_user.get_password_auth_params()
        token_params["scope"] = scope_test

        token_info = client.post(
            url=auth_test1_user.get_token_url(endpoint=client.endpoint),
            data=token_params,
        ).json()

        id_token = decode_id_token(token_info["id_token"])
        assert token_info["scope"] == scope_test
        assert id_token["project_id"] is None

    def test_get_scoped_token_no_project_one_organization_success(
        self, user_api_client, auth_test1_user, decode_id_token, scope_test
    ):
        client = user_api_client(auth_test1_user)
        org = client.create_organization("OrganizationName1")
        token_params = auth_test1_user.get_password_auth_params()
        token_params["scope"] = scope_test

        token_info = client.post(
            url=auth_test1_user.get_token_url(endpoint=client.endpoint),
            data=token_params,
        ).json()

        id_token = decode_id_token(token_info["id_token"])
        assert token_info["scope"] == scope_test
        assert id_token["project_id"] is None

        # cleanup
        client.delete_organization(org["uuid"])

    def test_get_scoped_token_one_project_one_organization_success(
        self, user_api_client, auth_test1_user, decode_id_token, scope_test
    ):
        client = user_api_client(auth_test1_user)
        org = client.create_organization("OrganizationName1")
        project = client.create_project(
            org["uuid"], "ProjectName1", uuid=TEST_PROJECT_ID
        )
        token_params = auth_test1_user.get_password_auth_params()
        token_params["scope"] = scope_test

        token_info = client.post(
            url=auth_test1_user.get_token_url(endpoint=client.endpoint),
            data=token_params,
        ).json()

        id_token = decode_id_token(token_info["id_token"])
        assert token_info["scope"] == scope_test
        assert id_token["project_id"] == project["uuid"]

        # cleanup
        client.delete_project(project["uuid"])
        client.delete_organization(org["uuid"])

    def test_get_scoped_token_two_project_two_organization_success(
        self, user_api_client, auth_test1_user, decode_id_token, scope_test
    ):
        client = user_api_client(auth_test1_user)
        org1 = client.create_organization("OrganizationName1")
        project1 = client.create_project(
            org1["uuid"], "ProjectName1Org1", uuid=TEST_PROJECT_ID
        )
        project2 = client.create_project(org1["uuid"], "ProjectName2Org1")
        org2 = client.create_organization("OrganizationName2")
        project3 = client.create_project(org2["uuid"], "ProjectName1Org2")
        project4 = client.create_project(org2["uuid"], "ProjectName2Org2")
        token_params = auth_test1_user.get_password_auth_params()
        token_params["scope"] = scope_test

        token_info = client.post(
            url=auth_test1_user.get_token_url(endpoint=client.endpoint),
            data=token_params,
        ).json()

        id_token = decode_id_token(token_info["id_token"])
        assert token_info["scope"] == scope_test
        assert id_token["project_id"] == project1["uuid"]

        # cleanup
        for p in [project4, project3, project2, project1]:
            client.delete_project(p["uuid"])
        for o in [org2, org1]:
            client.delete_organization(o["uuid"])

    def test_refresh_token_wo_scope_success(
        self, user_api_client, auth_test1_user, decode_id_token
    ):
        client = user_api_client(auth_test1_user)
        token_params = auth_test1_user.get_password_auth_params()
        token_params["scope"] = "test"
        token_info = client.post(
            url=auth_test1_user.get_token_url(endpoint=client.endpoint),
            data=token_params,
        ).json()

        refreshed_token_info = client.post(
            url=auth_test1_user.get_token_url(endpoint=client.endpoint),
            data={
                "grant_type": "refresh_token",
                "refresh_token": token_info["refresh_token"],
            },
        ).json()

        first_id_token = decode_id_token(token_info["id_token"])
        second_id_token = decode_id_token(refreshed_token_info["id_token"])
        assert token_info["scope"] == "test"
        assert first_id_token["project_id"] is None
        assert refreshed_token_info["scope"] == "test"
        assert second_id_token["project_id"] is None

    def test_refresh_to_scoped_token_one_project_one_organization_success(
        self, user_api_client, auth_test1_user, decode_id_token, scope_test
    ):
        client = user_api_client(auth_test1_user)
        org = client.create_organization("OrganizationName1")
        project = client.create_project(
            org["uuid"], "ProjectName1", uuid=TEST_PROJECT_ID
        )
        token_params = auth_test1_user.get_password_auth_params()
        token_info = client.post(
            url=auth_test1_user.get_token_url(endpoint=client.endpoint),
            data=token_params,
        ).json()

        refreshed_token_info = client.post(
            url=auth_test1_user.get_token_url(endpoint=client.endpoint),
            data={
                "grant_type": "refresh_token",
                "refresh_token": token_info["refresh_token"],
                "scope": scope_test,
            },
        ).json()

        first_id_token = decode_id_token(token_info["id_token"])
        second_id_token = decode_id_token(refreshed_token_info["id_token"])
        assert token_info["scope"] == ""
        assert first_id_token["project_id"] is None
        assert refreshed_token_info["scope"] == scope_test
        assert second_id_token["project_id"] == project["uuid"]

        # cleanup
        client.delete_project(project["uuid"])
        client.delete_organization(org["uuid"])

    def test_garbage_refresh_token_with_scope_error(
        self, user_api_client, auth_test1_user
    ):
        client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.BadRequestError):
            client.post(
                url=auth_test1_user.get_token_url(endpoint=client.endpoint),
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": "1234",  # garbage
                    "scope": "project:859aaa4f-9fcb-4433-8bc0-a84232a1177f",
                },
            )

    def test_invalid_grant_type_error(self, user_api_client, auth_test1_user):
        client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.BadRequestError):
            client.post(
                url=auth_test1_user.get_token_url(endpoint=client.endpoint),
                data={"grant_type": "obviously invalid"},
            )

    @pytest.fixture(scope="function")
    def service_token_environment(
        self,
        user_api_client,
        auth_user_admin,
        auth_test1_user,
        default_client_uuid,
        default_client_id,
        default_client_secret,
    ):
        """
        Fixture to set up common environment for service token tests.
        Returns a dict with all necessary objects for testing.
        """
        # Create admin client with all necessary permissions
        admin_client = user_api_client(
            auth_user_admin,
            permissions=[
                iam_c.PERMISSION_USER_CREATE,
                iam_c.PERMISSION_SERVICE_TOKEN_CREATE,
            ],
        )

        # Create users
        regular_user = admin_client.create_user(
            username="regular-user-service-token",
            password="regularpassword",
            email="regular-service-token@test.com",
        )

        service_user = admin_client.create_user(
            username="service-user-token",
            password="servicepassword",
            email="service-token@test.com",
            type="service",
        )

        # Create organization and project
        org = admin_client.create_organization(name="TestServiceTokenOrg")
        project = admin_client.create_project(
            name="TestServiceTokenProject",
            organization_uuid=org["uuid"],
        )

        # Get user objects for role bindings
        regular_user_obj = iam_models.User.objects.get_one(
            filters={"uuid": regular_user["uuid"]}
        )
        auth_test1_user_obj = iam_models.User.objects.get_one(
            filters={"uuid": auth_test1_user.uuid}
        )
        service_user_obj = iam_models.User.objects.get_one(
            filters={"uuid": service_user["uuid"]}
        )

        # Get roles
        owner_role = iam_models.Role.objects.get_one(
            filters={"uuid": common_c.OWNER_ROLE_UUID}
        )
        newcomer_role = iam_models.Role.objects.get_one(
            filters={"uuid": common_c.NEWCOMER_ROLE_UUID}
        )
        project_obj = iam_models.Project.objects.get_one(
            filters={"uuid": project["uuid"]}
        )

        # Confirm email for regular user
        admin_client.confirm_email(
            user_uuid=regular_user_obj.uuid,
            code=str(regular_user_obj.confirmation_code),
        )

        # Create auth object for regular user
        regular_user_auth = iam_clients.GenesisCoreAuth(
            username=regular_user["username"],
            password="regularpassword",
            client_uuid=default_client_uuid,
            client_id=default_client_id,
            client_secret=default_client_secret,
        )

        return {
            "admin_client": admin_client,
            "regular_user": regular_user,
            "regular_user_obj": regular_user_obj,
            "regular_user_auth": regular_user_auth,
            "service_user": service_user,
            "service_user_obj": service_user_obj,
            "org": org,
            "project": project,
            "project_obj": project_obj,
            "owner_role": owner_role,
            "newcomer_role": newcomer_role,
            "auth_test1_user_obj": auth_test1_user_obj,
        }

    def _setup_role_bindings(
        self, environment, regular_role="owner", service_role="owner"
    ):
        """Helper to setup role bindings for users."""
        regular_role_obj = (
            environment["owner_role"]
            if regular_role == "owner"
            else environment["newcomer_role"]
        )
        service_role_obj = (
            environment["owner_role"]
            if service_role == "owner"
            else environment["newcomer_role"]
        )

        regular_binding = iam_models.RoleBinding(
            user=environment["regular_user_obj"],
            role=regular_role_obj,
            project=environment["project_obj"],
        )
        regular_binding.save()

        service_binding = iam_models.RoleBinding(
            user=environment["service_user_obj"],
            role=service_role_obj,
            project=environment["project_obj"],
        )
        service_binding.save()

        return regular_binding, service_binding

    def test_get_service_token_with_access_token_success(
        self, user_api_client, service_token_environment, decode_id_token
    ):
        """
        Test successful service token exchange using user token.

        The test verifies that:
        1. User authenticates with their regular token
        2. Requests service account token with grant_type=access_token
        3. Gets service account token (not user token)
        4. Service account must have access to same project
        """
        env = service_token_environment

        # Setup role bindings with owner permissions
        regular_binding, service_binding = self._setup_role_bindings(
            env, regular_role="owner", service_role="owner"
        )

        # Create client for regular user
        regular_user_client = user_api_client(
            env["regular_user_auth"],
            permissions=[],
        )

        # Get service token using user token
        service_token_info = regular_user_client.post(
            url=env["regular_user_auth"].get_token_url(
                endpoint=regular_user_client.endpoint
            ),
            data={
                "grant_type": "access_token",
                "service_account_uuid": str(env["service_user"]["uuid"]),
                "scope": f"project:{env['project']['uuid']}",
            },
        ).json()

        # Verify service token response
        assert "access_token" in service_token_info
        assert "id_token" in service_token_info
        assert "token_type" in service_token_info
        assert service_token_info["token_type"] == "Bearer"
        assert "refresh_token" in service_token_info
        assert service_token_info["scope"] == f"project:{env['project']['uuid']}"

        # Decode and verify token is for service account, not regular user
        access_token = service_token_info["access_token"]
        decoded_token = decode_id_token(access_token)
        assert decoded_token["sub"] == str(env["service_user"]["uuid"])
        assert decoded_token["sub"] != str(env["regular_user"]["uuid"])

        # cleanup
        regular_binding.delete()
        service_binding.delete()

    @pytest.mark.parametrize(
        "test_scenario",
        [
            {
                "name": "no_permission",
                "regular_role": "newcomer",
                "service_role": "newcomer",
                "request_data": {
                    "grant_type": "access_token",
                    "service_account_uuid": None,
                    "scope": None,
                },
                "expected_error": bazooka_exc.ForbiddenError,
            },
            {
                "name": "no_service_account_uuid",
                "regular_role": "owner",
                "service_role": "owner",
                "request_data": {
                    "grant_type": "access_token",
                    "service_account_uuid": None,
                    "scope": None,
                },
                "expected_error": bazooka_exc.BadRequestError,
                "override_data": {"service_account_uuid": None},
            },
            {
                "name": "no_project_scope",
                "regular_role": "owner",
                "service_role": "owner",
                "request_data": {
                    "grant_type": "access_token",
                    "service_account_uuid": None,
                    "scope": "user:profile",
                },
                "expected_error": bazooka_exc.BadRequestError,
            },
        ],
    )
    def test_get_service_token_with_access_token_negative_scenarios(
        self, user_api_client, service_token_environment, test_scenario
    ):
        """
        Test negative scenarios for service token requests.
        """
        env = service_token_environment

        # Setup role bindings according to scenario
        regular_binding, service_binding = self._setup_role_bindings(
            env,
            regular_role=test_scenario["regular_role"],
            service_role=test_scenario["service_role"],
        )

        # Create client for regular user
        regular_user_client = user_api_client(
            env["regular_user_auth"],
            permissions=[],
        )

        # Prepare request data
        request_data = {
            "grant_type": "access_token",
            "service_account_uuid": str(env["service_user"]["uuid"]),
            "scope": f"project:{env['project']['uuid']}",
        }

        # Override data for specific scenarios
        if "override_data" in test_scenario:
            request_data.update(test_scenario["override_data"])
        elif test_scenario["name"] == "no_service_account_uuid":
            del request_data["service_account_uuid"]
        elif test_scenario["name"] == "no_project_scope":
            request_data["scope"] = "user:profile"

        # Test the request should fail
        with pytest.raises(test_scenario["expected_error"]):
            regular_user_client.post(
                url=env["regular_user_auth"].get_token_url(
                    endpoint=regular_user_client.endpoint
                ),
                data=request_data,
            )

        # cleanup
        regular_binding.delete()
        service_binding.delete()

    def test_get_regular_token_with_access_token_grant_type_error(
        self,
        user_api_client,
        auth_user_admin,
        auth_test1_user,
        default_client_uuid,
        default_client_id,
        default_client_secret,
    ):
        """
        Test that access_token grant type cannot be used to get regular user tokens.

        This ensures that grant_type=access_token can ONLY be used
        for service account token exchange, not for regular authentication.
        """
        # Create admin client with all necessary permissions
        admin_client = user_api_client(
            auth_user_admin,
            permissions=[
                iam_c.PERMISSION_USER_CREATE,
                iam_c.PERMISSION_SERVICE_TOKEN_CREATE,
            ],
        )

        # Create regular user with service token permissions
        regular_user_with_perm = admin_client.create_user(
            username="regular-user-with-perm-error",
            password="regularpassword",
            email="regular-with-perm-error@test.com",
        )

        # Create organization and project
        org = admin_client.create_organization(
            name="TestErrorOrg",
        )
        project = admin_client.create_project(
            name="TestErrorProject",
            organization_uuid=org["uuid"],
        )

        # Get user objects for role bindings
        regular_user_with_perm_obj = iam_models.User.objects.get_one(
            filters={"uuid": regular_user_with_perm["uuid"]}
        )
        auth_test1_user_obj = iam_models.User.objects.get_one(
            filters={"uuid": auth_test1_user.uuid}
        )

        # Add regular user as owner to project
        owner_role = iam_models.Role.objects.get_one(
            filters={"uuid": common_c.OWNER_ROLE_UUID}
        )
        project_obj = iam_models.Project.objects.get_one(
            filters={"uuid": project["uuid"]}
        )

        regular_binding = iam_models.RoleBinding(
            user=auth_test1_user_obj,
            role=owner_role,
            project=project_obj,
        )
        regular_binding.save()

        # Add regular user with permissions to project
        regular_with_perm_binding = iam_models.RoleBinding(
            user=regular_user_with_perm_obj,
            role=owner_role,
            project=project_obj,
        )
        regular_with_perm_binding.save()

        # Confirm email for regular user with permissions
        regular_user_with_perm_obj = iam_models.User.objects.get_one(
            filters={"uuid": regular_user_with_perm["uuid"]}
        )
        admin_client.confirm_email(
            user_uuid=regular_user_with_perm_obj.uuid,
            code=str(regular_user_with_perm_obj.confirmation_code),
        )

        # Create auth object for regular user with permissions
        regular_user_with_perm_auth = iam_clients.GenesisCoreAuth(
            username=regular_user_with_perm["username"],
            password="regularpassword",
            client_uuid=default_client_uuid,
            client_id=default_client_id,
            client_secret=default_client_secret,
        )
        # Create client for regular user with permissions
        regular_user_client = user_api_client(
            regular_user_with_perm_auth,
            permissions=[],
        )

        # Try to get regular user token with access_token grant type (should fail)
        with pytest.raises(bazooka_exc.BadRequestError):
            regular_user_client.post(
                url=regular_user_with_perm_auth.get_token_url(
                    endpoint=regular_user_client.endpoint
                ),
                data={
                    "grant_type": "access_token",
                    "username": regular_user_with_perm["username"],
                    "password": "regularpassword",
                },
            )

        # cleanup
        regular_with_perm_binding.delete()
        regular_binding.delete()
        admin_client.delete_project(project["uuid"])
        admin_client.delete_organization(org["uuid"])
        admin_client.delete_user(regular_user_with_perm["uuid"])
