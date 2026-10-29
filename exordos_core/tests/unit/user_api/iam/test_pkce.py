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

import datetime
from types import SimpleNamespace
from unittest import mock

import pytest
from gcl_iam import exceptions as iam_exc

from exordos_core.user_api.iam import exceptions
from exordos_core.user_api.iam.dm import models

# RFC 7636 Appendix B interoperability vector.
VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


@pytest.fixture
def exchange():
    client = object()
    info = SimpleNamespace(
        code_challenge=CHALLENGE,
        code_challenge_method="S256",
        idp=SimpleNamespace(iam_client=client),
        redirect_uri="https://client.example/callback",
        expiration_time_at=datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(minutes=5),
        token=object(),
        delete=mock.Mock(),
    )
    with (
        mock.patch.object(models.IdpAuthorizationInfo, "_get_engine") as engine,
        mock.patch.object(type(models.IdpAuthorizationInfo.objects), "get_all") as get,
    ):
        get.return_value = [info]
        yield client, info, engine, get


def redeem(client, info, verifier=None):
    return models.IamClient.get_token_by_authorization_code(
        client, "code", info.redirect_uri, verifier
    )


def test_s256_and_replay(exchange):
    client, info, engine, get = exchange
    assert redeem(client, info, VERIFIER) is info.token
    session = engine.return_value.session_manager.return_value.__enter__.return_value
    assert get.call_args.kwargs["locked"] is True
    assert get.call_args.kwargs["session"] is session
    info.delete.assert_called_once_with(session=session)
    get.return_value = []
    with pytest.raises(iam_exc.CredentialsAreInvalidError):
        redeem(client, info, VERIFIER)


@pytest.mark.parametrize(
    "verifier", [None, "", "x" * 42, "x" * 129, "é" * 43, "!" * 43, "x" * 43]
)
def test_invalid_verifier_does_not_consume_code(exchange, verifier):
    client, info, _, _ = exchange
    with pytest.raises(exceptions.InvalidPkceGrant):
        redeem(client, info, verifier)
    info.delete.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("code_challenge_method", "plain"),
        ("token", None),
        (
            "expiration_time_at",
            datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc),
        ),
    ],
)
def test_invalid_grant(exchange, field, value):
    client, info, _, _ = exchange
    setattr(info, field, value)
    with pytest.raises(exceptions.InvalidPkceGrant):
        redeem(client, info, VERIFIER)
    info.delete.assert_not_called()


def test_client_binding(exchange):
    _, info, _, _ = exchange
    with pytest.raises(exceptions.InvalidPkceGrant):
        redeem(object(), info, VERIFIER)
    info.delete.assert_not_called()


def test_redirect_binding(exchange):
    client, info, _, _ = exchange
    with pytest.raises(exceptions.InvalidPkceGrant):
        models.IamClient.get_token_by_authorization_code(
            client, "code", "https://other.example", VERIFIER
        )
    info.delete.assert_not_called()


def test_legacy_exchange(exchange):
    client, info, _, _ = exchange
    info.code_challenge = None
    info.code_challenge_method = None
    assert redeem(client, info) is info.token
    info.delete.assert_called_once()


def test_verifier_without_challenge_is_rejected(exchange):
    client, info, _, _ = exchange
    info.code_challenge = None
    with pytest.raises(exceptions.InvalidPkceGrant):
        redeem(client, info, VERIFIER)
    info.delete.assert_not_called()


@pytest.mark.parametrize(
    "challenge,method",
    [
        (None, "S256"),
        ("", "S256"),
        (CHALLENGE, None),
        (CHALLENGE, "plain"),
        (CHALLENGE, "s256"),
        ("x" * 42, "S256"),
        ("x" * 44, "S256"),
        ("!" * 43, "S256"),
    ],
)
def test_authorize_rejects_invalid_pkce(challenge, method):
    with pytest.raises(exceptions.InvalidPkceRequest):
        models.Idp.authorize(
            object(),
            "client",
            "uri",
            "state",
            "code",
            "openid",
            code_challenge=challenge,
            code_challenge_method=method,
        )


@pytest.mark.parametrize("challenge,method", [(None, None), (CHALLENGE, "S256")])
def test_authorize_persists_pkce(challenge, method):
    idp = SimpleNamespace(
        client_id="client",
        callback=mock.Mock(),
        nonce_required=False,
        iam_client=SimpleNamespace(uuid="client"),
        uuid="idp",
    )
    with (
        mock.patch.object(models, "IdpAuthorizationInfo") as info,
        mock.patch.object(models.contexts, "get_context") as ctx,
    ):
        ctx.return_value.get_real_url_with_prefix.return_value = "https://iam.example"
        models.Idp.authorize(
            idp,
            "client",
            "uri",
            "state",
            "code",
            "openid",
            code_challenge=challenge,
            code_challenge_method=method,
        )
        assert info.call_args.kwargs["code_challenge"] == challenge
        assert info.call_args.kwargs["code_challenge_method"] == method
        info.return_value.insert.assert_called_once()


def test_authorize_controller_forwards_pkce():
    from exordos_core.user_api.iam.api import controllers

    resource = mock.Mock()
    controller = object.__new__(controllers.IdpController)
    controller.authorize._get(
        controller,
        resource,
        "client",
        "uri",
        "state",
        "code",
        "openid",
        code_challenge=CHALLENGE,
        code_challenge_method="S256",
    )
    assert resource.authorize.call_args.kwargs["code_challenge"] == CHALLENGE
    assert resource.authorize.call_args.kwargs["code_challenge_method"] == "S256"


def test_token_controller_forwards_verifier():
    from exordos_core.user_api.iam.api import controllers

    resource = mock.Mock()
    controller = object.__new__(controllers.ClientsController)
    with mock.patch.object(controller, "get_context"):
        response = controller.get_token._post(
            controller,
            resource,
            "authorization_code",
            code="code",
            redirect_uri="uri",
            code_verifier=VERIFIER,
        )
    resource.get_token_by_authorization_code.assert_called_once_with(
        code="code",
        redirect_uri="uri",
        code_verifier=VERIFIER,
    )
    assert (
        response
        is resource.get_token_by_authorization_code.return_value.get_response_body.return_value
    )


def test_pkce_failure_is_oauth_invalid_grant():
    from exordos_core.common.api.middlewares import errors

    handler = object.__new__(errors.ErrorsHandlerMiddleware)
    request = mock.Mock()
    handler._construct_error_response(request, exceptions.InvalidPkceGrant())
    assert request.ResponseClass.call_args.kwargs["status"] == 400
    assert request.ResponseClass.call_args.kwargs["json"]["error"] == "invalid_grant"
