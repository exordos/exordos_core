#    Copyright 2026 Genesis Corporation.
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

from contextlib import nullcontext
import json
from types import SimpleNamespace
from unittest import mock

from bazooka import exceptions as bazooka_exc
from gcl_iam import exceptions as iam_exc
import pytest
import requests
import webob

from exordos_core.common.api.middlewares import errors
from exordos_core.user_api.iam import exceptions
from exordos_core.user_api.iam.api import openapi_specs
from exordos_core.user_api.iam.clients import keycloak
from exordos_core.user_api.iam.dm import models


@pytest.fixture
def kc_response():
    response = requests.Response()
    response.status_code = 400
    response.url = (
        "https://kc.example/auth/realms/company/protocol/openid-connect/token"
    )
    response._content = json.dumps(
        {
            "error": "invalid_grant",
            "error_description": "Account is not fully set up",
        }
    ).encode()
    # Exercise Bazooka's actual HTTP-error wrapping, not just a mocked post().
    with mock.patch("requests.sessions.Session.request", return_value=response):
        yield response


def check_password():
    return keycloak.KeycloakClient("https://kc.example/auth").check_password(
        "company", "core", "client-secret", "user@example.com", "password"
    )


def test_required_action_response(kc_response):
    with pytest.raises(exceptions.KeycloakRequiredAction) as caught:
        check_password()

    req = webob.Request.blank("/")
    response = errors.ErrorsHandlerMiddleware(None)._construct_error_response(
        req, caught.value
    )
    assert response.status_int == 400
    assert response.json == {
        "error": "external_action_required",
        "error_description": "Complete the required actions in Keycloak, then sign in again.",
        "redirect_url": "https://kc.example/auth/realms/company/account/",
    }
    responses = openapi_specs.OA_SPEC_GET_TOKEN_KWARGS["responses"]
    schema = responses[400]["content"]["application/json"]["schema"]
    assert schema["anyOf"][0] == {"$ref": "#/components/schemas/Error"}
    required_action = schema["anyOf"][1]
    assert set(required_action["required"]) == set(response.json)
    for field, value in response.json.items():
        prop = required_action["properties"][field]
        assert isinstance(value, str)
        if "enum" in prop:
            assert value in prop["enum"]
    assert (
        required_action["properties"]["error_description"]["example"]
        == response.json["error_description"]
    )
    assert 200 in responses and 401 in responses and "default" in responses
    assert "password" not in response.text
    assert "client-secret" not in response.text


def test_success(kc_response):
    kc_response.status_code = 200
    kc_response._content = b'{"access_token": "token"}'
    assert check_password() is True


def test_invalid_password_does_not_redirect(kc_response):
    kc_response._content = (
        b'{"error":"invalid_grant","error_description":"Invalid user credentials"}'
    )
    assert check_password() is False


@pytest.mark.parametrize("status", [400, 401])
def test_client_configuration_error_is_not_required_action(kc_response, status):
    kc_response.status_code = status
    kc_response._content = b'{"error":"invalid_client"}'
    with pytest.raises(bazooka_exc.ClientError):
        check_password()


def test_malformed_error_is_not_required_action(kc_response):
    kc_response._content = b"not json"
    with pytest.raises(ValueError):
        check_password()


@pytest.mark.parametrize("outcome", [True, False, TimeoutError("unavailable")])
def test_keycloak_is_always_checked_and_password_is_not_cached(outcome):
    source = models.KeycloakUserSource(
        endpoint="https://kc.example/auth",
        realm="company",
        client_id="core",
        client_secret="client-secret",
    )
    user = mock.Mock(email="user@example.com")
    user.check_secret.return_value = True
    with mock.patch.object(keycloak.KeycloakClient, "check_password") as check:
        if isinstance(outcome, Exception):
            check.side_effect = outcome
            expected = pytest.raises(TimeoutError)
        else:
            check.return_value = outcome
            expected = (
                pytest.raises(iam_exc.CredentialsAreInvalidError)
                if not outcome
                else nullcontext()
            )
        with expected:
            source.process_secret(user, "password")
        check.assert_called_once_with(
            realm="company",
            client_id="core",
            client_secret="client-secret",
            login="user@example.com",
            password="password",
        )
    user.check_secret.assert_not_called()
    user.update.assert_not_called()
    assert "secret" not in vars(user)


def test_local_user_password_check_is_unchanged():
    user = SimpleNamespace(check_secret=mock.Mock(return_value=False))
    with pytest.raises(iam_exc.CredentialsAreInvalidError):
        models.IamUserSource().process_secret(user, "password")
    user.check_secret.assert_called_once_with("password")
