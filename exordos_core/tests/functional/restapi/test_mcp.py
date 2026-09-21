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

"""The MCP service against a running User API, over HTTP between the two."""

import json

import bazooka
from gcl_iam.tests.functional import clients as iam_clients
import pytest

CLIENT = bazooka.Client(default_timeout=60)


def rpc(mcp_api, method, params=None, token=None):
    message = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        message["params"] = params
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return CLIENT.post(mcp_api, json=message, headers=headers)


def call_tool(mcp_api, token, name, /, **arguments):
    response = rpc(
        mcp_api,
        "tools/call",
        {"name": name, "arguments": arguments},
        token=token,
    )
    result = response.json()["result"]
    return result["content"][0]["text"], result["isError"]


def token_of(client):
    return client.authenticate()["access_token"]


class TestMcp:
    def test_requires_authorization(self, user_api, mcp_api):
        with pytest.raises(bazooka.exceptions.UnauthorizedError):
            rpc(mcp_api, "ping")

    def test_call_api_as_admin(
        self,
        user_api,
        mcp_api,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        text, is_error = call_tool(
            mcp_api,
            token_of(client),
            "call_api",
            method="GET",
            path="/v1/iam/users/",
            query={"username": auth_user_admin.username},
        )

        assert not is_error
        status, body = text.split("\n", 1)
        assert status == "HTTP 200 OK"
        assert [user["username"] for user in json.loads(body)] == [
            auth_user_admin.username
        ]

    def test_list_users_reaches_the_user_api(
        self,
        user_api,
        mcp_api,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        """A resource tool, end to end, over the wire between the services."""
        client = user_api_client(auth_user_admin)

        text, is_error = call_tool(
            mcp_api,
            token_of(client),
            "list_users",
            username=auth_user_admin.username,
        )

        assert not is_error
        status, body = text.split("\n", 1)
        assert status == "HTTP 200 OK"
        assert [user["username"] for user in json.loads(body)] == [
            auth_user_admin.username
        ]

    def test_call_api_keeps_caller_permissions(
        self,
        user_api,
        mcp_api,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_test1_user: iam_clients.GenesisCoreAuth,
    ):
        """The User API decides; this service only carries the credentials."""
        client = user_api_client(auth_test1_user)

        text, is_error = call_tool(
            mcp_api,
            token_of(client),
            "create_role",
            name="not-allowed",
        )

        assert is_error
        assert text.startswith("HTTP 403")

    def test_discovery(self, user_api, mcp_api, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)
        token = token_of(client)

        text, is_error = call_tool(
            mcp_api, token, "list_endpoints", search="em/elements"
        )

        assert not is_error
        assert "GET /v1/em/elements/ - Get Elements" in text.splitlines()

        text, is_error = call_tool(
            mcp_api,
            token,
            "describe_endpoint",
            method="GET",
            path="/v1/em/elements/{ElementUuid}",
        )

        assert not is_error
        assert "$ref" not in text

    def test_the_user_api_no_longer_serves_mcp(self, user_api, user_api_noauth_client):
        """It moved out; what is left must not answer for it."""
        client = user_api_noauth_client()

        with pytest.raises(bazooka.exceptions.BaseHTTPException):
            client.post(
                f"{client.endpoint}mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            )
