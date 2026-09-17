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

from bazooka import exceptions as bazooka_exc
from gcl_iam.tests.functional import clients as iam_clients
import pytest


def call_tool(client, name, **arguments):
    response = client.post(
        f"{client.endpoint}mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    result = response.json()["result"]
    return result["content"][0]["text"], result["isError"]


class TestMcp:
    def test_requires_authorization(self, user_api_noauth_client):
        client = user_api_noauth_client()

        with pytest.raises(bazooka_exc.UnauthorizedError):
            client.post(
                f"{client.endpoint}mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            )

    def test_call_api_as_admin(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        text, is_error = call_tool(
            client,
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

    def test_call_api_keeps_caller_permissions(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_test1_user: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_test1_user)

        text, is_error = call_tool(
            client,
            "call_api",
            method="POST",
            path="/v1/iam/roles/",
            body={"name": "not-allowed"},
        )

        assert is_error
        assert text.startswith("HTTP 403")
