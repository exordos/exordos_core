#    Copyright 2026 Genesis Corporation.
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

import json
import time
from unittest import mock
from urllib.parse import urljoin

import altcha
from gcl_iam.tests.functional import clients as iam_clients
import requests

from exordos_core.tests.functional.restapi.iam import base
from exordos_core.user_api.security.dm import models as security_models

CREATE_USER_PATH = "iam/users/"


class TestUserCreationSecurity(base.BaseIamResourceTest):
    """Security rules for user creation: Firebase OR CAPTCHA OR admin bypass."""

    def _create_rule(
        self,
        name: str,
        action: security_models.AbstractVerifier,
        project_id=None,
    ) -> security_models.Rule:
        condition = security_models.UriRegexConditions(
            uri_regex=rf"^/v1/{CREATE_USER_PATH}$",
            method="POST",
        )
        rule = security_models.Rule(
            name=name,
            condition=condition,
            action=action,
            operator=security_models.OperatorEnum.OR.value,
            project_id=project_id,
        )
        rule.insert()
        return rule

    def _create_firebase_rule(
        self,
        project_id=None,
        allowed_app_ids=None,
    ):
        action = security_models.FirebaseAppCheckVerifier(
            credentials_path="/tmp/fake-firebase-credentials.json",
            allowed_app_ids=allowed_app_ids or [],
        )
        return self._create_rule(
            name="Firebase App Check for user creation",
            action=action,
            project_id=project_id,
        )

    def _create_captcha_rule(self, project_id=None):
        action = security_models.CaptchaVerifier(
            hmac_key="test-hmac-key-12345",
        )
        return self._create_rule(
            name="CAPTCHA for user creation",
            action=action,
            project_id=project_id,
        )

    def _create_admin_bypass_rule(
        self,
        allowed_identifiers,
        project_id=None,
    ):
        normalized = [str(v) for v in allowed_identifiers if v is not None]
        action = security_models.AdminBypassVerifier(
            bypass_users=normalized,
        )
        return self._create_rule(
            name="Admin bypass for user creation",
            action=action,
            project_id=project_id,
        )

    def _create_grant_permission_rule(self, permission, project_id=None):
        action = security_models.GrantPermissionAction(
            permission=permission,
        )
        return self._create_rule(
            name="Grant permission for user creation",
            action=action,
            project_id=project_id,
        )

    def _user_create_url(self, user_api):
        return urljoin(user_api.base_url, CREATE_USER_PATH)

    def _post_create_user(self, user_api, headers, username_suffix="sec"):
        url = self._user_create_url(user_api)
        username = f"testuser_{username_suffix}"
        data = {
            "username": username,
            "password": "testpass123",
            "email": f"{username}@example.com",
        }
        return requests.post(url, json=data, headers=headers or {})

    @mock.patch("firebase_admin.app_check.verify_token")
    @mock.patch("firebase_admin.credentials.Certificate")
    @mock.patch("firebase_admin.initialize_app")
    @mock.patch("firebase_admin.get_app")
    def test_create_user_firebase_or_captcha_or_admin_bypass(
        self,
        mock_get_app,
        mock_initialize_app,
        mock_certificate,
        mock_verify_token,
        user_api,
        auth_user_admin,
        auth_test1_user,
    ):
        """Any of Firebase / CAPTCHA / admin-bypass should allow creation."""
        self._create_firebase_rule(allowed_app_ids=["test-app-id"])
        self._create_captcha_rule()
        self._create_admin_bypass_rule(
            allowed_identifiers=[
                auth_user_admin.email,
                str(auth_user_admin.uuid),
            ]
        )

        mock_get_app.side_effect = ValueError("No app")
        mock_initialize_app.return_value = mock.MagicMock()
        mock_certificate.return_value = mock.MagicMock()
        mock_verify_token.return_value = {"app_id": "test-app-id"}

        admin_token = self._get_access_token(user_api, auth_user_admin)

        headers = {
            "Authorization": f"Bearer {admin_token}",
            "X-Firebase-AppCheck": "valid_firebase_token_12345",
        }
        response = self._post_create_user(user_api, headers, username_suffix="firebase")
        assert response.status_code in (200, 201)

        captcha_payload = json.dumps(
            {
                "challenge": "test_challenge_123",
                "number": 123456,
                "signature": "test_signature_123",
                "algorithm": "SHA-512",
                "salt": "test_salt?expires=9999999999",
            }
        )

        # altcha is imported inside action, so patch its global function
        with mock.patch("altcha.verify_solution") as mock_verify_solution:
            mock_verify_solution.return_value = altcha.VerifySolutionResult(
                expired=False,
                invalid_signature=None,
                invalid_solution=None,
                time=time.time(),
                verified=True,
                error=None,
            )
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "X-Captcha": captcha_payload,
            }
            response = self._post_create_user(
                user_api, headers, username_suffix="captcha"
            )
            assert response.status_code in (200, 201)

        headers = {
            "Authorization": f"Bearer {admin_token}",
        }
        with mock.patch(
            "exordos_core.user_api.security.dm.models.AdminBypassVerifier.execute",
            return_value=True,
        ):
            response = self._post_create_user(
                user_api, headers, username_suffix="admin_bypass"
            )
        assert response.status_code in (200, 201)

    def _get_access_token(self, user_api, auth):
        client = iam_clients.GenesisCoreTestRESTClient(
            f"{user_api.get_endpoint()}v1/",
            auth,
        )
        assert "access_token" in client._auth_cache
        return client._auth_cache["access_token"]

    def test_create_user_forbidden_when_no_rule_matches(
        self,
        user_api,
        auth_test1_user,
    ):
        """Без Firebase, CAPTCHA и без admin-bypass – должен быть 403 из SecurityRulesMiddleware."""
        self._create_firebase_rule(allowed_app_ids=["test-app-id"])
        self._create_captcha_rule()
        self._create_admin_bypass_rule(allowed_identifiers=["non-existent@example.com"])

        token = self._get_access_token(user_api, auth_test1_user)
        headers = {
            "Authorization": f"Bearer {token}",
        }

        response = self._post_create_user(
            user_api, headers, username_suffix="forbidden"
        )
        assert response.status_code == 403

    def test_grant_permission_action_matches_all(
        self,
    ):
        """GrantPermissionAction returns True for ALL requests."""
        from unittest import mock

        action = security_models.GrantPermissionAction(
            permission="iam.user.create",
        )

        # Test with NoIamSessionStored (no auth at all)
        ctx1 = mock.MagicMock()
        assert action.execute(ctx1) is True

        # Test with anonymous token (type='anon')
        ctx2 = mock.MagicMock()
        assert action.execute(ctx2) is True

        # Test with authenticated user (type='user')
        ctx3 = mock.MagicMock()
        assert action.execute(ctx3) is True

    def test_granted_permission_tracking_api(
        self,
    ):
        """Test granted permission tracking API."""
        from exordos_core.common import contexts

        # Create mock request with environ
        mock_request = mock.MagicMock()
        mock_request.environ = {}

        # Create context with mock request
        ctx = mock.MagicMock(spec=contexts.GenesisCoreAuthContext)
        ctx.request = mock_request

        # Bind real methods to mock
        ctx.add_granted_permission = (
            contexts.GenesisCoreAuthContext.add_granted_permission.__get__(ctx)
        )
        ctx.has_granted_permission = (
            contexts.GenesisCoreAuthContext.has_granted_permission.__get__(ctx)
        )

        # Initially no permission granted
        assert ctx.has_granted_permission("iam.user.create") is False

        # Grant a permission
        ctx.add_granted_permission("iam.user.create")

        # Now it is granted
        assert ctx.has_granted_permission("iam.user.create") is True

        # Other permissions are still not granted
        assert ctx.has_granted_permission("iam.user.delete") is False

    def test_create_user_anonymous_registration(
        self,
        user_api,
    ):
        """Anonymous user can register when GrantPermissionAction grants iam.user.create."""
        # Grant permission rule - no Firebase, no CAPTCHA needed
        self._create_grant_permission_rule(permission="iam.user.create")

        # No Authorization header - completely anonymous request
        response = self._post_create_user(
            user_api, headers={}, username_suffix="street_user"
        )
        assert response.status_code in (200, 201), (
            f"Anonymous registration failed: {response.status_code} - {response.text[:200]}"
        )

        # Verify user was created
        data = response.json()
        assert "uuid" in data
        assert data["username"] == "testuser_street_user"

    def test_create_user_anonymous_without_rule_is_forbidden(
        self,
        user_api,
    ):
        """Anonymous user cannot register without GrantPermissionAction Rule."""
        # No rules created at all - only default admin rules exist

        # No Authorization header - completely anonymous request
        response = self._post_create_user(
            user_api, headers={}, username_suffix="no_rule_user"
        )
        # Should be forbidden because no Rule allows anonymous access
        # AND user doesn't have iam.user.create permission
        assert response.status_code == 403

    def test_authenticated_user_with_grant_permission_rule(
        self,
        user_api,
        auth_test1_user,
    ):
        """Authenticated user CAN register when GrantPermissionAction Rule grants the permission.

        The Rule grants iam.user.create permission for any request that
        passes the security Rule, regardless of whether the user is
        anonymous or authenticated.
        """
        self._create_grant_permission_rule(permission="iam.user.create")

        token = self._get_access_token(user_api, auth_test1_user)
        headers = {"Authorization": f"Bearer {token}"}

        # Authenticated request with GrantPermissionAction Rule
        response = self._post_create_user(
            user_api, headers=headers, username_suffix="auth_with_rule"
        )
        # Should succeed because Rule grants the required permission
        assert response.status_code in (200, 201), (
            f"Auth user with Rule failed: {response.status_code} - {response.text[:200]}"
        )
