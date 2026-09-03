# Copyright 2026 Genesis Corporation
#
# All Rights Reserved.
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

import pytest
import webob

from exordos_core.user_api.api import middlewares


def _application(environ, start_response):
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [b"payload"]


def _build_middleware(allowed_origins):
    return middlewares.CorsMiddleware(
        application=_application,
        allowed_origins=allowed_origins,
    )


def test_request_without_origin_is_untouched():
    # Given
    middleware = _build_middleware(["https://example.com"])
    request = webob.Request.blank("/v1/")

    # When
    response = request.get_response(middleware)

    # Then
    assert response.body == b"payload"
    assert "Access-Control-Allow-Origin" not in response.headers


def test_disallowed_origin_gets_no_cors_headers():
    # Given
    middleware = _build_middleware(["https://example.com"])
    request = webob.Request.blank("/v1/")
    request.headers["Origin"] = "https://evil.com"

    # When
    response = request.get_response(middleware)

    # Then
    assert response.body == b"payload"
    assert "Access-Control-Allow-Origin" not in response.headers


@pytest.mark.parametrize(
    "allowed_origins",
    [["https://example.com"], ["*"]],
)
def test_allowed_origin_gets_cors_headers(allowed_origins):
    # Given
    middleware = _build_middleware(allowed_origins)
    request = webob.Request.blank("/v1/")
    request.headers["Origin"] = "https://example.com"

    # When
    response = request.get_response(middleware)

    # Then
    assert response.body == b"payload"
    assert response.headers["Access-Control-Allow-Origin"] == "https://example.com"
    assert "Origin" in response.headers["Vary"]


def test_preflight_is_answered_without_calling_the_application():
    # Given
    middleware = _build_middleware(["https://example.com"])
    request = webob.Request.blank("/v1/", method="OPTIONS")
    request.headers["Origin"] = "https://example.com"
    request.headers["Access-Control-Request-Method"] = "POST"
    request.headers["Access-Control-Request-Headers"] = "Authorization"

    # When
    response = request.get_response(middleware)

    # Then
    assert response.status_code == 204
    assert response.body == b""
    assert response.headers["Access-Control-Allow-Origin"] == "https://example.com"
    assert response.headers["Access-Control-Allow-Methods"] == "POST"
    assert response.headers["Access-Control-Allow-Headers"] == "Authorization"
    assert response.headers["Access-Control-Max-Age"] == str(
        middlewares.PREFLIGHT_MAX_AGE
    )


def test_plain_options_request_reaches_the_application():
    # Given
    middleware = _build_middleware(["https://example.com"])
    request = webob.Request.blank("/v1/", method="OPTIONS")
    request.headers["Origin"] = "https://example.com"

    # When
    response = request.get_response(middleware)

    # Then
    assert response.body == b"payload"
    assert response.headers["Access-Control-Allow-Origin"] == "https://example.com"
