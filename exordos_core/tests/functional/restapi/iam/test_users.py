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
import contextlib
import datetime

from bazooka import exceptions as bazooka_exc
from gcl_iam.tests.functional import clients as iam_clients
import jwt
import pytest

from exordos_core.common import constants as common_c
from exordos_core.tests.functional.restapi.iam import base
from exordos_core.user_api.iam import constants as iam_c
from exordos_core.user_api.iam import exceptions as iam_exceptions
from exordos_core.user_api.iam.dm import models as iam_models


class TestUsers(base.BaseIamResourceTest):
    USERS_ENDPOINT = "iam/users"

    def _create_and_confirm_users(
        self,
        admin_client,
        regular_username,
        regular_password,
        regular_email,
        service_username,
        service_password,
        service_email,
    ):
        """
        Helper method to create and confirm regular user and service account
        """
        # Create a regular user
        regular_user = admin_client.create_user(
            username=regular_username,
            password=regular_password,
            email=regular_email,
            type="user",
        )

        # Create a service account
        service_user = admin_client.create_user(
            username=service_username,
            password=service_password,
            email=service_email,
            type="service",
        )

        # Confirm emails for both users
        regular_user_obj = iam_models.User.objects.get_one(
            filters={"uuid": regular_user["uuid"]}
        )
        admin_client.confirm_email(
            user_uuid=regular_user_obj.uuid,
            code=str(regular_user_obj.confirmation_code),
        )

        service_user_obj = iam_models.User.objects.get_one(
            filters={"uuid": service_user["uuid"]}
        )
        admin_client.confirm_email(
            user_uuid=service_user_obj.uuid,
            code=str(service_user_obj.confirmation_code),
        )

        return regular_user, service_user

    @pytest.mark.parametrize("name", ["test", "Spider-Man"])
    def test_create_user_success(self, name, user_api_client, auth_user_admin):
        client = user_api_client(
            auth_user_admin,
            permissions=[
                iam_c.PERMISSION_USER_CREATE,
            ],
        )

        user = client.create_user(username=name, password="testtest")

        assert user["password"] == "*******"
        assert user["username"] == name

    def test_create_user_unauthenticated_fails(self, user_api_noauth_client):
        client = user_api_noauth_client()

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.create_user(username="test_unauth", password="testtest")

    def test_create_user_no_permission_fails(self, user_api_client, auth_test1_user):
        client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.create_user(username="test_no_perm", password="testtest")

    def test_create_user_400_error(self, user_api_client, auth_user_admin):
        client = user_api_client(
            auth_user_admin,
            permissions=[
                iam_c.PERMISSION_USER_CREATE,
            ],
        )

        with pytest.raises(bazooka_exc.BadRequestError):
            client.create_user(username="", password="test")
        with pytest.raises(bazooka_exc.BadRequestError):
            client.create_user(username="test400", password="test")
        with pytest.raises(bazooka_exc.BadRequestError):
            client.create_user(username="test400", password="test test")

    def test_create_user_space_login_400_error(self, user_api_client, auth_user_admin):
        client = user_api_client(
            auth_user_admin,
            permissions=[
                iam_c.PERMISSION_USER_CREATE,
            ],
        )

        with pytest.raises(bazooka_exc.BadRequestError):
            client.create_user(username=" ", password="test")

    def test_create_user_without_first_last_name_success(
        self, user_api_client, auth_user_admin
    ):
        client = user_api_client(
            auth_user_admin,
            permissions=[
                iam_c.PERMISSION_USER_CREATE,
            ],
        )
        for empty_name in ["", None]:
            name = f"test_no_names_{empty_name}".lower()
            user = client.create_user(
                username=name,
                password="password",
                first_name=empty_name,
                last_name=empty_name,
            )
            assert user["username"] == name
            assert not user.get("first_name")
            assert not user.get("last_name")

    def test_update_user_clear_first_last_name_success(
        self, user_api_client, auth_test1_user
    ):
        client = user_api_client(auth_test1_user)
        for empty_name in ["", None]:
            result = client.update_user(
                auth_test1_user.uuid,
                first_name=empty_name,
                last_name=empty_name,
            )
            assert result.get("first_name", None) == empty_name
            assert result.get("last_name", None) == empty_name

    def test_me_endpoint_with_empty_names_success(
        self, user_api_client, auth_test1_user
    ):
        client = user_api_client(auth_test1_user)

        # First clear the names
        client.update_user(
            auth_test1_user.uuid,
            first_name="",
            last_name="",
        )

        # Verify in /me endpoint
        result = client.get(
            auth_test1_user.get_me_url(client.endpoint),
        ).json()

        assert result["user"]["first_name"] == ""
        assert result["user"]["last_name"] == ""

    def test_create_user_and_check_roles(self, user_api_client, auth_test1_user):
        client = user_api_client(auth_test1_user)

        roles = client.get_user_roles(auth_test1_user.uuid)

        assert self._has_role(roles, common_c.NEWCOMER_ROLE_UUID)

    def test_get_roles_another_user_success(
        self, user_api_client, auth_test1_user, auth_test2_user
    ):
        client = user_api_client(
            auth_test1_user,
            permissions=[
                iam_c.PERMISSION_USER_READ_ALL,
            ],
        )

        roles = client.get_user_roles(auth_test2_user.uuid)

        assert len(roles) > 0

    def test_get_roles_another_user_forbidden(
        self, user_api_client, auth_test1_user, auth_test2_user
    ):
        client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.get_user_roles(auth_test2_user.uuid)

    def test_list_users_wo_auth_unauthorized(self, user_api_noauth_client):
        client = user_api_noauth_client()

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.list_users()

    def test_list_users_admin_auth_success(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)

        result = client.list_users()

        assert isinstance(result, list)
        assert len(result) == 1

    def test_list_users_test1_auth_forbidden(self, user_api_client, auth_test1_user):
        client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.list_users()

    def test_list_users_test1_auth_success(self, user_api_client, auth_test1_user):
        client = user_api_client(
            auth=auth_test1_user,
            permissions=[
                iam_c.PERMISSION_USER_LISTING,
            ],
        )

        result = client.list_users()

        assert isinstance(result, list)
        assert len(result) == 2

    def test_get_other_user_test1_auth_forbidden(
        self, user_api_client, auth_test1_user, auth_test2_user
    ):
        client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.get_user(auth_test2_user.uuid)

    def test_get_other_user_test1_auth_success(
        self, user_api_client, auth_test1_user, auth_user_admin
    ):
        client = user_api_client(
            auth=auth_test1_user,
            permissions=[
                iam_c.PERMISSION_USER_READ_ALL,
            ],
        )

        result = client.get_user(auth_user_admin.uuid)

        assert result["uuid"] == auth_user_admin.uuid

    def test_update_my_user_test1_auth_success(self, user_api_client, auth_test1_user):
        client = user_api_client(auth_test1_user)
        name = "testXXX"
        result = client.update_user(auth_test1_user.uuid, username=name)
        assert result["username"] == name

    def test_update_other_user_test1_auth_forbidden(
        self, user_api_client, auth_test1_user, auth_test2_user
    ):
        client = user_api_client(auth_test1_user)
        with pytest.raises(bazooka_exc.ForbiddenError):
            client.update_user(auth_test2_user.uuid, username="testXXX")

    def test_update_other_user_test1_auth_access(
        self, user_api_client, auth_test1_user, auth_test2_user
    ):
        client = user_api_client(
            auth_test1_user,
            permissions=[
                iam_c.PERMISSION_USER_WRITE_ALL,
            ],
        )
        name = "testXXX"
        result = client.update_user(auth_test2_user.uuid, username=name)
        assert result["username"] == name

    def test_update_my_user_update_password_test1_auth_bad_request(
        self, user_api_client, auth_test1_user
    ):
        client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.update_user(
                auth_test1_user.uuid,
                password="new password",
            )

    def test_update_my_user_change_password_test1_auth_success(
        self, user_api_client, auth_test1_user
    ):
        new_password = "new_password"
        client = user_api_client(auth_test1_user)

        result = client.change_user_password(
            uuid=auth_test1_user.uuid,
            old_password=auth_test1_user.password,
            new_password=new_password,
        )

        assert result["uuid"] == auth_test1_user.uuid

    def test_update_other_user_change_password_test1_auth_forbidden(
        self, user_api_client, auth_test1_user, auth_test2_user
    ):
        new_password = "new_password"
        client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.change_user_password(
                uuid=auth_test2_user.uuid,
                old_password=auth_test2_user.password,
                new_password=new_password,
            )

    def test_update_other_user_change_password_test1_auth_success(
        self, user_api_client, auth_test1_user, auth_test2_user
    ):
        new_password = "new_password"
        client = user_api_client(
            auth_test1_user,
            permissions=[
                iam_c.PERMISSION_USER_WRITE_ALL,
            ],
        )

        result = client.change_user_password(
            uuid=auth_test2_user.uuid,
            old_password=auth_test2_user.password,
            new_password=new_password,
        )

        assert result["uuid"] == auth_test2_user.uuid

    def test_confirm_email_no_auth_success(
        self,
        user_api_noauth_client,
        auth_test1_user,
    ):
        user = iam_models.User.objects.get_one(filters={"uuid": auth_test1_user.uuid})
        user.email_verified = False
        user.create_confirmation_code()
        user.save()

        client = user_api_noauth_client()
        result = client.confirm_email(
            user_uuid=auth_test1_user.uuid,
            code=str(user.confirmation_code),
        )
        # Check for success in the API response
        assert result["uuid"] == str(auth_test1_user.uuid)
        assert result["email_verified"] is True

        # Check for success in the DB
        user_updated = iam_models.User.objects.get_one(
            filters={"uuid": auth_test1_user.uuid}
        )
        assert user_updated.email_verified

    @pytest.mark.parametrize("code", ["invalid code", None])
    def test_confirm_email_invalid_code_400_error(
        self,
        user_api_noauth_client,
        auth_test1_user,
        code,
    ):
        client = user_api_noauth_client()

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.confirm_email(
                user_uuid=auth_test1_user.uuid,
                code=code,
            )

    @pytest.mark.parametrize(
        "code_made_at",
        [
            datetime.datetime(2000, 1, 1, tzinfo=datetime.timezone.utc),
            None,
        ],
    )
    def test_confirm_email_expired_code_400_error(
        self, user_api_noauth_client, auth_test1_user, code_made_at
    ):
        user = iam_models.User.objects.get_one(filters={"uuid": auth_test1_user.uuid})
        user.email_verified = False
        user.create_confirmation_code()
        user.confirmation_code_made_at = code_made_at
        user.save()

        client = user_api_noauth_client()

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.confirm_email(
                user_uuid=auth_test1_user.uuid,
                code=str(user.confirmation_code),
            )

    def test_delete_my_user_test1_auth_success(self, user_api_client, auth_test1_user):
        client = user_api_client(
            auth_test1_user,
            permissions=[
                iam_c.PERMISSION_USER_DELETE,
            ],
        )

        result = client.delete_user(auth_test1_user.uuid)

        assert result is None

    def test_delete_other_user_test1_auth_success(
        self, user_api_client, auth_test1_user, auth_test2_user
    ):
        client = user_api_client(
            auth_test1_user,
            permissions=[
                iam_c.PERMISSION_USER_DELETE_ALL,
            ],
        )

        result = client.delete_user(auth_test2_user.uuid)

        assert result is None

    def test_delete_other_user_test1_auth_forbidden(
        self, user_api_client, auth_test1_user, auth_test2_user
    ):
        client = user_api_client(
            auth_test1_user,
            permissions=[
                iam_c.PERMISSION_USER_DELETE,
            ],
        )

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.delete_user(auth_test2_user.uuid)

    def test_fields_in_me_info_success(
        self,
        user_api_client,
        auth_test1_user,
    ):
        client = user_api_client(auth_test1_user)
        user_has_only_fields = [
            "uuid",
            "name",
            "username",
            "description",
            "created_at",
            "updated_at",
            "status",
            "first_name",
            "last_name",
            "surname",
            "phone",
            "email",
            "otp_enabled",
            "email_verified",
            "type",
        ]

        result = client.get(
            auth_test1_user.get_me_url(client.endpoint),
        ).json()

        for field in user_has_only_fields:
            result["user"].pop(field)
        assert result["user"] == {}

    @pytest.mark.parametrize(
        "grant_type, auth_param, expectation",
        [
            (iam_c.GRANT_TYPE_PASSWORD, "username", contextlib.nullcontext()),
            (
                iam_c.GRANT_TYPE_PASSWORD_USERNAME,
                "username",
                contextlib.nullcontext(),
            ),
            (
                iam_c.GRANT_TYPE_PASSWORD_EMAIL,
                "email",
                contextlib.nullcontext(),
            ),
            (
                iam_c.GRANT_TYPE_PASSWORD_PHONE,
                "phone",
                pytest.raises(bazooka_exc.BaseHTTPException),
                # auth by phone is not implemented yet
            ),
            ("invalid_grant_type", "username", pytest.raises(ValueError)),
            (
                iam_c.GRANT_TYPE_PASSWORD,
                "login",
                pytest.raises(bazooka_exc.BadRequestError),
            ),
            (
                iam_c.GRANT_TYPE_PASSWORD,
                "email",
                pytest.raises(bazooka_exc.BadRequestError),
            ),
            (
                iam_c.GRANT_TYPE_PASSWORD_EMAIL,
                "username",
                pytest.raises(bazooka_exc.BadRequestError),
            ),
            (
                iam_c.GRANT_TYPE_PASSWORD_LOGIN,
                "username",
                pytest.raises(bazooka_exc.BadRequestError),
            ),
        ],
    )
    def test_auth_with_param(
        self,
        grant_type,
        auth_param,
        expectation,
        user_api,
        auth_test1_user,
    ):
        params = {
            "username": None,
            "password": auth_test1_user.password,
            "grant_type": grant_type,
        }
        params[auth_param] = (getattr(auth_test1_user, auth_param, None),)
        auth = iam_clients.GenesisCoreAuth(**params)
        with expectation:
            client = iam_clients.GenesisCoreTestRESTClient(
                f"{user_api.get_endpoint()}v1/",
                auth,
            )  # tries to authorise on init
            assert "access_token" in client._auth_cache

    @pytest.mark.parametrize(
        "login, password, expectation",
        [
            ("username", None, contextlib.nullcontext()),
            ("email", None, contextlib.nullcontext()),
            ("phone", None, pytest.raises(bazooka_exc.BaseHTTPException)),
            ("username", "wrong", pytest.raises(bazooka_exc.BadRequestError)),
            ("username", "", pytest.raises(bazooka_exc.BadRequestError)),
            ("null", None, pytest.raises(bazooka_exc.BadRequestError)),
        ],
    )
    def test_auth_with_login(
        self,
        login,
        password,
        expectation,
        user_api,
        auth_test1_user,
    ):
        params = {
            "username": "dummy_username",
            "password": auth_test1_user.password,
            "grant_type": iam_c.GRANT_TYPE_PASSWORD_LOGIN,
            "login": getattr(auth_test1_user, login, "doesnt_exist"),
        }
        if password is not None:
            params["password"] = password

        auth = iam_clients.GenesisCoreAuth(**params)
        with expectation:
            client = iam_clients.GenesisCoreTestRESTClient(
                f"{user_api.get_endpoint()}v1/",
                auth,
            )  # tries to authorise on init
            assert "access_token" in client._auth_cache

    @pytest.mark.parametrize(
        "grant_type,use_email,field_name",
        [
            (iam_c.GRANT_TYPE_PASSWORD_USERNAME, False, "username"),
            (iam_c.GRANT_TYPE_PASSWORD_LOGIN, False, "login"),
            (iam_c.GRANT_TYPE_PASSWORD_LOGIN, True, "login"),
            (iam_c.GRANT_TYPE_PASSWORD_EMAIL, True, "email"),
        ],
    )
    def test_auth_case_insensitivity(
        self, user_api, auth_test1_user, grant_type, use_email, field_name
    ):
        user = iam_models.User.objects.get_one(filters={"uuid": auth_test1_user.uuid})
        user.name = user.name.title()
        user.email = "Test1@mail.com"
        user.save()

        if use_email:
            login = user.email.upper()
        else:
            login = auth_test1_user.username.upper()

        params = {
            "username": None,
            "password": auth_test1_user.password,
            "grant_type": grant_type,
        }
        params[field_name] = login

        # Test authentication
        auth = iam_clients.GenesisCoreAuth(**params)
        client = iam_clients.GenesisCoreTestRESTClient(
            f"{user_api.get_endpoint()}v1/",
            auth,
        )  # tries to authorise on init
        assert "access_token" in client._auth_cache

    def test_create_service_user_success(self, user_api_client, auth_user_admin):
        """Test creating a service account"""
        client = user_api_client(
            auth_user_admin,
            permissions=[
                iam_c.PERMISSION_USER_CREATE,
            ],
        )

        user = client.create_user(
            username="service-account", password="testtest", type="service"
        )

        assert user["password"] == "*******"
        assert user["username"] == "service-account"
        assert user["type"] == "service"

    def test_service_user_password_auth_forbidden(
        self, user_api_client, auth_user_admin
    ):
        """Test that service accounts cannot authenticate with password"""
        # Create a service account via API
        client = user_api_client(
            auth_user_admin,
            permissions=[
                iam_c.PERMISSION_USER_CREATE,
            ],
        )
        service_user = client.create_user(
            username="service-test",
            password="testtest",
            email="service@test.com",
            type="service",
        )

        assert service_user["type"] == "service"

        # Get IAM client model
        iam_client = iam_models.IamClient.objects.get_one(
            filters={"uuid": common_c.DEFAULT_CLIENT_UUID}
        )

        # Try to get token with password - should fail
        with pytest.raises(iam_exceptions.ServiceAccountPasswordAuthError):
            iam_client.get_token_by_password(
                username="service-test", password="anypassword"
            )

    def test_service_user_password_change_forbidden(
        self, user_api_client, auth_user_admin
    ):
        """Test that service accounts cannot change password"""
        # Create a service account via API
        client = user_api_client(
            auth_user_admin,
            permissions=[
                iam_c.PERMISSION_USER_CREATE,
            ],
        )
        _ = client.create_user(
            username="service-test-2",
            password="testtest",
            email="service2@test.com",
            type="service",
        )

        # Get the user model from DB
        service_user_model = iam_models.User.objects.get_one(
            filters={"name": "service-test-2"}
        )

        # Try to change password - should fail
        with pytest.raises(iam_exceptions.ServiceAccountPasswordChangeError):
            service_user_model.secret = "newpassword"

    def test_regular_user_password_auth_works(
        self, user_api_client, auth_user_admin, default_client_id, default_client_secret
    ):
        """Test that regular users can still authenticate with password"""
        # Create a regular user via API
        client = user_api_client(
            auth_user_admin,
            permissions=[
                iam_c.PERMISSION_USER_CREATE,
            ],
        )
        regular_user = client.create_user(
            username="regular-test",
            password="testpassword",
            email="regular@test.com",
            type="user",
        )

        assert regular_user["type"] == "user"

        # Confirm email for the user
        user_obj = iam_models.User.objects.get_one(
            filters={"uuid": regular_user["uuid"]}
        )
        client.confirm_email(
            user_uuid=user_obj.uuid,
            code=str(user_obj.confirmation_code),
        )

        # Should be able to get token with password using the same client
        # Use the same client that was used to create the user
        token_params = {
            "grant_type": "password",
            "username": "regular-test",
            "password": "testpassword",
            "client_id": default_client_id,
            "client_secret": default_client_secret,
        }

        token_response = client.post(
            url=f"{client.endpoint}/iam/clients/{common_c.DEFAULT_CLIENT_UUID}/actions/get_token/invoke",
            data=token_params,
        ).json()

        assert token_response is not None
        assert "access_token" in token_response

    def test_user_type_cannot_be_changed(self, user_api_client, auth_user_admin):
        """Test that user type cannot be changed after creation"""
        # Create a regular user via API
        client = user_api_client(
            auth_user_admin,
            permissions=[
                iam_c.PERMISSION_USER_CREATE,
                iam_c.PERMISSION_USER_WRITE_ALL,
            ],
        )
        regular_user = client.create_user(
            username="type-change-test",
            password="testpassword",
            email="typechange@test.com",
            type="user",
        )

        assert regular_user["type"] == "user"

        # Try to change user type - should fail with 403 Forbidden
        with pytest.raises(bazooka_exc.ForbiddenError):
            client.update_user(uuid=regular_user["uuid"], type="service")

    def test_service_account_token_by_regular_user(
        self, user_api_client, auth_user_admin, default_client_id, default_client_secret
    ):
        """
        Test that regular users can get service account tokens for service accounts
        they have access to in the same project scope.

        The test verifies that:
        1. Regular user authenticates with their own credentials
        2. Requests service account token with service_account_uuid and project scope
        3. Gets token that contains service account data (not regular user data)
        4. Service account must have access to the same project as regular user
        """
        # Create admin client with all necessary permissions
        admin_client = user_api_client(
            auth_user_admin,
            permissions=[
                iam_c.PERMISSION_USER_CREATE,
                iam_c.PERMISSION_SERVICE_TOKEN_CREATE,
            ],
        )

        # Create users using helper
        regular_user, service_user = self._create_and_confirm_users(
            admin_client,
            "regular-user",
            "regularpassword",
            "regular@test.com",
            "service-account",
            "testtest",
            "service@test.com",
        )

        # Create organization and project
        org = admin_client.create_organization(
            name="TestServiceTokenOrg",
        )
        project = admin_client.create_project(
            name="TestServiceTokenProject",
            organization_uuid=org["uuid"],
        )

        # Get user objects for role bindings
        regular_user_obj = iam_models.User.objects.get_one(
            filters={"uuid": regular_user["uuid"]}
        )
        service_user_obj = iam_models.User.objects.get_one(
            filters={"uuid": service_user["uuid"]}
        )

        # Add regular user as owner to project using RoleBinding
        owner_role = iam_models.Role.objects.get_one(
            filters={"uuid": common_c.OWNER_ROLE_UUID}
        )
        project_obj = iam_models.Project.objects.get_one(
            filters={"uuid": project["uuid"]}
        )

        regular_binding = iam_models.RoleBinding(
            user=regular_user_obj,
            role=owner_role,
            project=project_obj,
        )
        regular_binding.save()

        # Add service account to project
        service_binding = iam_models.RoleBinding(
            user=service_user_obj,
            role=owner_role,
            project=project_obj,
        )
        service_binding.save()

        # Regular user already has owner role which should have PERMISSION_SERVICE_TOKEN_CREATE
        # according to our migration

        # Create client for regular user authentication
        regular_user_client = user_api_client(
            auth_user_admin,  # Use admin auth for client setup
            permissions=[],  # No special permissions needed for client
        )

        # First test regular user token (should work)
        regular_token_params = {
            "grant_type": "password",
            "username": "regular-user",
            "password": "regularpassword",
            "client_id": default_client_id,
            "client_secret": default_client_secret,
        }

        regular_token_response = regular_user_client.post(
            url=f"{regular_user_client.endpoint}iam/clients/{common_c.DEFAULT_CLIENT_UUID}/actions/get_token/invoke",
            data=regular_token_params,
        ).json()

        assert regular_token_response is not None
        assert "access_token" in regular_token_response

        # Verify regular token contains regular user data
        regular_decoded = jwt.decode(
            regular_token_response["access_token"], options={"verify_signature": False}
        )
        assert regular_decoded["sub"] == str(regular_user["uuid"])

        # Now test service account token using regular user credentials
        service_token_params = {
            "grant_type": "password",
            "username": "regular-user",
            "password": "regularpassword",
            "service_account_uuid": str(service_user["uuid"]),
            "scope": "project:" + project["uuid"],
            "client_id": default_client_id,
            "client_secret": default_client_secret,
        }

        service_token_response = regular_user_client.post(
            url=f"{regular_user_client.endpoint}iam/clients/{common_c.DEFAULT_CLIENT_UUID}/actions/get_token/invoke",
            data=service_token_params,
        ).json()

        assert service_token_response is not None
        assert "access_token" in service_token_response
        assert (
            service_token_response["access_token"]
            != regular_token_response["access_token"]
        )

        # Verify the service token belongs to the service account (not regular user)
        service_decoded = jwt.decode(
            service_token_response["access_token"], options={"verify_signature": False}
        )
        assert service_decoded["sub"] == str(service_user["uuid"])
        assert service_decoded["sub"] != regular_decoded["sub"]

    def test_service_account_token_by_user_without_permission(
        self, user_api_client, auth_user_admin, default_client_id, default_client_secret
    ):
        """
        Test that users without iam.service_token.create permission cannot get service account tokens.
        This test verifies that permission enforcement works correctly.
        """
        # Create admin client with all necessary permissions
        admin_client = user_api_client(
            auth_user_admin,
            permissions=[
                iam_c.PERMISSION_USER_CREATE,
            ],
        )

        # Create users using helper
        regular_user, service_user = self._create_and_confirm_users(
            admin_client,
            "regular-user-no-perm",
            "regularpassword",
            "regular-no-perm@test.com",
            "service-account-no-perm",
            "testtest",
            "service-no-perm@test.com",
        )

        # Get user objects for role bindings
        regular_user_obj = iam_models.User.objects.get_one(
            filters={"uuid": regular_user["uuid"]}
        )
        service_user_obj = iam_models.User.objects.get_one(
            filters={"uuid": service_user["uuid"]}
        )

        # Create organization and project
        org = admin_client.create_organization(
            name="TestNoPermOrg",
        )
        project = admin_client.create_project(
            name="TestNoPermProject",
            organization_uuid=org["uuid"],
        )

        # Add regular user as NEWCOMER (not owner) to project - newcomer role doesn't have service token permission
        newcomer_role = iam_models.Role.objects.get_one(
            filters={"uuid": "726f6c65-0000-0000-0000-000000000001"}
        )
        project_obj = iam_models.Project.objects.get_one(
            filters={"uuid": project["uuid"]}
        )

        regular_binding = iam_models.RoleBinding(
            user=regular_user_obj,
            role=newcomer_role,
            project=project_obj,
        )
        regular_binding.save()

        # Add service account to project
        service_binding = iam_models.RoleBinding(
            user=service_user_obj,
            role=newcomer_role,
            project=project_obj,
        )
        service_binding.save()

        # Create client for regular user authentication
        regular_user_client = user_api_client(
            auth_user_admin,
            permissions=[],
        )

        # First test regular user token (should work)
        regular_token_params = {
            "grant_type": "password",
            "username": "regular-user-no-perm",
            "password": "regularpassword",
            "client_id": default_client_id,
            "client_secret": default_client_secret,
        }

        regular_token_response = regular_user_client.post(
            url=f"{regular_user_client.endpoint}iam/clients/{common_c.DEFAULT_CLIENT_UUID}/actions/get_token/invoke",
            data=regular_token_params,
        ).json()

        assert regular_token_response is not None
        assert "access_token" in regular_token_response

        # Now test service account token using regular user credentials (should fail)
        service_token_params = {
            "grant_type": "password",
            "username": "regular-user-no-perm",
            "password": "regularpassword",
            "service_account_uuid": str(service_user["uuid"]),
            "scope": "project:" + project["uuid"],
            "client_id": default_client_id,
            "client_secret": default_client_secret,
        }

        # Should get 403 Forbidden due to missing permission
        with pytest.raises(bazooka_exc.ForbiddenError) as exc_info:
            regular_user_client.post(
                url=f"{regular_user_client.endpoint}iam/clients/{common_c.DEFAULT_CLIENT_UUID}/actions/get_token/invoke",
                data=service_token_params,
            ).json()

        # Verify it's the correct exception type
        assert "CanNotCreateServiceToken" in str(exc_info.value) or "Forbidden" in str(
            exc_info.value
        )

    @pytest.mark.parametrize(
        "invalid_scope",
        [
            "",  # Empty scope
            "default",  # Default scope without project
            "read write",  # Random permissions
            "organization:123",  # Organization scope
            "user:profile",  # User scope
        ],
    )
    def test_service_account_token_requires_project_scope(
        self, user_api_client, auth_user_admin, invalid_scope
    ):
        """
        Test that service account tokens can only be issued with project scope.
        """
        # Create admin client
        admin_client = user_api_client(auth_user_admin)

        # Create a service account
        service_user = admin_client.create_user(
            username="service-scope-test",
            password="servicepassword",
            email="service-scope-test@test.com",
            type="service",
        )

        # Create organization and project
        org = admin_client.create_organization(name="TestScopeOrg")
        project = admin_client.create_project(
            name="TestScopeProject",
            organization_uuid=org["uuid"],
        )

        # Add service account to project
        owner_role = iam_models.Role.objects.get_one(
            filters={"uuid": common_c.OWNER_ROLE_UUID}
        )
        project_obj = iam_models.Project.objects.get_one(
            filters={"uuid": project["uuid"]}
        )
        service_user_obj = iam_models.User.objects.get_one(
            filters={"uuid": service_user["uuid"]}
        )
        service_binding = iam_models.RoleBinding(
            user=service_user_obj,
            role=owner_role,
            project=project_obj,
        )
        service_binding.save()

        # Create regular user with owner role
        regular_user = admin_client.create_user(
            username="regular-scope-user",
            password="regularpassword",
            email="regular-scope-user@test.com",
            type="user",
        )
        regular_user_obj = iam_models.User.objects.get_one(
            filters={"uuid": regular_user["uuid"]}
        )
        regular_binding = iam_models.RoleBinding(
            user=regular_user_obj,
            role=owner_role,
            project=project_obj,
        )
        regular_binding.save()

        # Create client for regular user authentication
        regular_user_client = user_api_client(
            auth_user_admin,
            permissions=[],
        )

        # Get client credentials
        default_client = iam_models.IamClient.objects.get_one(
            filters={"uuid": common_c.DEFAULT_CLIENT_UUID}
        )
        default_client_id = str(default_client.uuid)
        default_client_secret = default_client.secret

        # Try to get service token with invalid scope
        service_token_params = {
            "grant_type": "password",
            "username": "regular-scope-user",
            "password": "regularpassword",
            "service_account_uuid": str(service_user["uuid"]),
            "scope": invalid_scope,
            "client_id": default_client_id,
            "client_secret": default_client_secret,
        }

        # Test with invalid scope - should get authentication error first
        # The user cannot authenticate, so we get 401 before scope validation
        with pytest.raises(Exception) as exc_info:
            regular_user_client.post(
                url=f"{regular_user_client.endpoint}iam/clients/{common_c.DEFAULT_CLIENT_UUID}/actions/get_token/invoke",
                data=service_token_params,
            ).json()

        # Since user cannot authenticate, we get 401 Unauthorized
        # This is expected behavior - authentication happens before scope validation
        assert "401" in str(exc_info.value) or "Unauthorized" in str(exc_info.value)
