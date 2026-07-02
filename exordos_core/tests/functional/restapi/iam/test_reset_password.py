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

from exordos_core.tests.functional.restapi.iam import base
from exordos_core.user_api.iam import constants as iam_c
from exordos_core.user_api.iam.dm import models as iam_models


class TestUserResetPassword(base.BaseIamResourceTest):
    """Tests for POST /v1/iam/users/<uuid>/actions/reset_password/invoke"""

    @pytest.fixture()
    def target_user(self, user_api_client, auth_user_admin):
        """Create and confirm a regular user for reset_password tests."""
        admin_client = user_api_client(
            auth_user_admin,
            permissions=[iam_c.PERMISSION_USER_CREATE],
        )
        password = "OldPassword1"
        user = admin_client.create_user(
            username="reset-pwd-target",
            password=password,
            email="reset-pwd-target@test.com",
        )
        user_obj = iam_models.User.objects.get_one(filters={"uuid": user["uuid"]})
        admin_client.confirm_email(
            user_uuid=user_obj.uuid,
            code=str(user_obj.confirmation_code),
        )
        return user, password

    def _reset_password_url(self, client, user_uuid):
        return client.build_resource_uri(
            ["iam/users", user_uuid, "actions/reset_password/invoke"]
        )

    def test_reset_password_with_valid_code_success(
        self, user_api_client, auth_user_admin, target_user
    ):
        """Reset password using valid confirmation code — no permission required."""
        user, _ = target_user
        admin_client = user_api_client(auth_user_admin)

        user_obj = iam_models.User.objects.get_one(filters={"uuid": user["uuid"]})
        user_obj.create_confirmation_code()
        code = str(user_obj.confirmation_code)

        new_password = "NewPassword1"
        result = admin_client.post(
            self._reset_password_url(admin_client, user["uuid"]),
            json={"new_password": new_password, "code": code},
        ).json()

        assert result["uuid"] == user["uuid"]

    def test_reset_password_with_permission_no_code_success(
        self, user_api_client, auth_user_admin, target_user
    ):
        """Reset password using iam.user.reset_password permission — no code required."""
        user, _ = target_user
        client = user_api_client(
            auth_user_admin,
            permissions=[iam_c.PERMISSION_USER_RESET_PASSWORD],
        )

        result = client.post(
            self._reset_password_url(client, user["uuid"]),
            json={"new_password": "NewPassword2"},
        ).json()

        assert result["uuid"] == user["uuid"]

    def test_reset_password_no_code_no_permission_forbidden(
        self, user_api_client, auth_test1_user, target_user
    ):
        """Without code and without permission — must return 403."""
        user, _ = target_user
        client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.post(
                self._reset_password_url(client, user["uuid"]),
                json={"new_password": "NewPassword3"},
            )

    def test_reset_password_new_password_works_for_login(
        self,
        user_api_client,
        auth_user_admin,
        target_user,
        default_client_uuid,
        default_client_id,
        default_client_secret,
    ):
        """After successful reset, user can log in with new password."""
        user, _ = target_user
        admin_client = user_api_client(
            auth_user_admin,
            permissions=[iam_c.PERMISSION_USER_RESET_PASSWORD],
        )
        new_password = "AfterReset1"

        admin_client.post(
            self._reset_password_url(admin_client, user["uuid"]),
            json={"new_password": new_password},
        )

        new_auth = iam_clients.GenesisCoreAuth(
            username=user["username"],
            password=new_password,
            client_uuid=default_client_uuid,
            client_id=default_client_id,
            client_secret=default_client_secret,
        )
        new_client = user_api_client(new_auth)
        me = new_client.me()

        assert me["user"]["uuid"] == user["uuid"]

    def test_reset_password_invalid_code_fails(
        self, user_api_client, auth_user_admin, target_user
    ):
        """Invalid confirmation code must result in 403 (CanNotConfirmUser)."""
        user, _ = target_user
        admin_client = user_api_client(auth_user_admin)

        with pytest.raises(bazooka_exc.ForbiddenError):
            admin_client.post(
                self._reset_password_url(admin_client, user["uuid"]),
                json={
                    "new_password": "NewPassword4",
                    "code": "00000000-0000-0000-0000-000000000000",
                },
            )

    def test_reset_password_weak_password_fails(
        self, user_api_client, auth_user_admin, target_user
    ):
        """Password that fails validation (too short) must return 400."""
        user, _ = target_user
        admin_client = user_api_client(
            auth_user_admin,
            permissions=[iam_c.PERMISSION_USER_RESET_PASSWORD],
        )

        with pytest.raises(bazooka_exc.BadRequestError):
            admin_client.post(
                self._reset_password_url(admin_client, user["uuid"]),
                json={"new_password": "short"},
            )


class TestSendResetPasswordCode(base.BaseIamResourceTest):
    """Tests for POST /v1/iam/clients/<uuid>/actions/send_reset_password_code/invoke"""

    @pytest.fixture()
    def target_user(self, user_api_client, auth_user_admin):
        """Create and confirm a user for send_reset_password_code tests."""
        admin_client = user_api_client(
            auth_user_admin,
            permissions=[iam_c.PERMISSION_USER_CREATE],
        )
        password = "SomePassword1"
        user = admin_client.create_user(
            username="send-reset-code-target",
            password=password,
            email="send-reset-code@test.com",
        )
        user_obj = iam_models.User.objects.get_one(filters={"uuid": user["uuid"]})
        admin_client.confirm_email(
            user_uuid=user_obj.uuid,
            code=str(user_obj.confirmation_code),
        )
        yield user, password
        admin_client.delete_user(user["uuid"])

    def _send_reset_code_url(self, client, client_uuid):
        return client.build_resource_uri(
            [
                "iam/clients",
                client_uuid,
                "actions/send_reset_password_code/invoke",
            ]
        )

    def test_send_reset_password_code_with_permission_success(
        self,
        user_api_client,
        auth_user_admin,
        target_user,
        default_client_uuid,
    ):
        """Admin with permission can send reset code."""
        user, _ = target_user
        client = user_api_client(
            auth_user_admin,
            permissions=[iam_c.PERMISSION_IAM_CLIENT_SEND_RESET_PASSWORD_CODE],
        )

        response = client.post(
            self._send_reset_code_url(client, default_client_uuid),
            json={"email": user["email"]},
        )

        assert response.status_code == 200

    def test_send_reset_password_code_creates_confirmation_code(
        self,
        user_api_client,
        auth_user_admin,
        target_user,
        default_client_uuid,
    ):
        """After sending, user gets a confirmation_code in DB."""
        user, _ = target_user
        client = user_api_client(
            auth_user_admin,
            permissions=[iam_c.PERMISSION_IAM_CLIENT_SEND_RESET_PASSWORD_CODE],
        )

        client.post(
            self._send_reset_code_url(client, default_client_uuid),
            json={"email": user["email"]},
        )

        user_obj = iam_models.User.objects.get_one(filters={"uuid": user["uuid"]})
        assert user_obj.confirmation_code is not None

    def test_send_reset_password_code_without_permission_forbidden(
        self,
        user_api_client,
        auth_test1_user,
        target_user,
        default_client_uuid,
    ):
        """User without permission gets 403."""
        user, _ = target_user
        client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.post(
                self._send_reset_code_url(client, default_client_uuid),
                json={"email": user["email"]},
            )

    def test_send_reset_password_code_nonexistent_email_no_error(
        self,
        user_api_client,
        auth_user_admin,
        default_client_uuid,
    ):
        """Non-existent email must not reveal user existence (no error)."""
        client = user_api_client(
            auth_user_admin,
            permissions=[iam_c.PERMISSION_IAM_CLIENT_SEND_RESET_PASSWORD_CODE],
        )

        response = client.post(
            self._send_reset_code_url(client, default_client_uuid),
            json={"email": "nonexistent@example.com"},
        )

        assert response.status_code == 200

    def test_send_reset_password_code_missing_email_bad_request(
        self,
        user_api_client,
        auth_user_admin,
        default_client_uuid,
    ):
        """Missing email must return 400 instead of 500."""
        client = user_api_client(
            auth_user_admin,
            permissions=[iam_c.PERMISSION_IAM_CLIENT_SEND_RESET_PASSWORD_CODE],
        )

        with pytest.raises(bazooka_exc.BadRequestError):
            client.post(
                self._send_reset_code_url(client, default_client_uuid),
                json={},
            )

    def test_send_reset_password_code_then_reset_password_flow(
        self,
        user_api_client,
        auth_user_admin,
        target_user,
        default_client_uuid,
        default_client_id,
        default_client_secret,
    ):
        """Full flow: send code → reset password with code → login with new password."""
        user, _ = target_user
        admin_client = user_api_client(
            auth_user_admin,
            permissions=[iam_c.PERMISSION_IAM_CLIENT_SEND_RESET_PASSWORD_CODE],
        )

        user_obj = iam_models.User.objects.get_one(filters={"uuid": user["uuid"]})
        original_secret_hash = user_obj.secret_hash
        assert user_obj.confirmation_code is None

        admin_client.post(
            self._send_reset_code_url(admin_client, default_client_uuid),
            json={"email": user["email"]},
        )

        user_obj = iam_models.User.objects.get_one(filters={"uuid": user["uuid"]})
        assert user_obj.confirmation_code is not None
        code = str(user_obj.confirmation_code)
        new_password = "FlowNewPass1"

        reset_url = admin_client.build_resource_uri(
            ["iam/users", user["uuid"], "actions/reset_password/invoke"]
        )
        admin_client.post(
            reset_url,
            json={"new_password": new_password, "code": code},
        )

        user_obj = iam_models.User.objects.get_one(filters={"uuid": user["uuid"]})
        assert user_obj.confirmation_code is None
        assert user_obj.secret_hash != original_secret_hash

        new_auth = iam_clients.GenesisCoreAuth(
            username=user["username"],
            password=new_password,
            client_uuid=default_client_uuid,
            client_id=default_client_id,
            client_secret=default_client_secret,
        )
        new_client = user_api_client(new_auth)
        me = new_client.me()

        assert me["user"]["uuid"] == user["uuid"]
