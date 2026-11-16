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

from types import SimpleNamespace
from unittest import mock

import pytest
from oslo_config import cfg

from exordos_core.user_api.iam.dm import models

INTERNAL_URL = "http://core.local.genesis-core.tech/api/core"
PUBLIC_URL = "https://oidc.example.com"


@pytest.fixture
def pin_public_base_url():
    def pin(value):
        cfg.CONF.set_override("public_base_url", value, group="iam")

    yield pin
    cfg.CONF.clear_override("public_base_url", group="iam")


@pytest.fixture
def request_url():
    with mock.patch.object(models.contexts, "get_context") as ctx:
        ctx.return_value.get_real_url_with_prefix.return_value = INTERNAL_URL
        yield ctx


def test_without_configuration_the_request_url_is_used(
    request_url, pin_public_base_url
):
    pin_public_base_url(None)
    assert models.resolve_public_base_url(request_url.return_value) == INTERNAL_URL


def test_configured_url_overrides_the_request_url(request_url, pin_public_base_url):
    pin_public_base_url(f"{PUBLIC_URL}/")
    assert models.resolve_public_base_url(request_url.return_value) == PUBLIC_URL


def test_scheme_conflict_is_logged(request_url, pin_public_base_url, caplog):
    pin_public_base_url(PUBLIC_URL)
    with caplog.at_level("WARNING"):
        models.resolve_public_base_url(request_url.return_value)
    assert "public_base_url" in caplog.text


def test_wellknown_info_is_built_from_the_configured_url(
    request_url,
    pin_public_base_url,
):
    pin_public_base_url(PUBLIC_URL)
    idp = SimpleNamespace(
        uuid="idp-uuid",
        iam_client=SimpleNamespace(
            uuid="client-uuid",
            get_id_token_signing_alg_values_supported=mock.Mock(return_value=["HS256"]),
        ),
    )
    info = models.Idp.get_wellknown_info(idp)
    assert info["issuer"] == f"{PUBLIC_URL}/v1/iam/clients/client-uuid"
    assert info["token_endpoint"].startswith(f"{PUBLIC_URL}/")
    assert info["authorization_endpoint"].startswith(f"{PUBLIC_URL}/")


def test_authorize_redirects_to_the_configured_url(
    request_url,
    pin_public_base_url,
):
    pin_public_base_url(PUBLIC_URL)
    idp = SimpleNamespace(
        client_id="client",
        callback=mock.Mock(),
        nonce_required=False,
        uuid="idp-uuid",
        iam_client=SimpleNamespace(uuid="client-uuid"),
    )
    with mock.patch.object(models, "IdpAuthorizationInfo") as info:
        info.return_value.uuid = "auth-uuid"
        location = models.Idp.authorize(
            idp,
            "client",
            "https://client.example/callback",
            "state",
            "code",
            "openid",
        )
    assert location.startswith(f"{PUBLIC_URL}/?auth_uuid=auth-uuid")
