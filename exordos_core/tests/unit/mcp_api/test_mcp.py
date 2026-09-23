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
import urllib.parse

from bazooka import exceptions as bazooka_exc
import pytest
import requests
import webob

from exordos_core.mcp_api.api import mcp

USER_API_URL = "https://exordos.example.com/api/core"


class EchoClient:
    """Answer a call with what the MCP service sent, instead of calling out."""

    def request(self, method, url, headers=None, json=None):
        parts = urllib.parse.urlsplit(url)
        status = 404 if parts.path.endswith("/v1/missing/") else 200
        resp = requests.Response()
        resp.status_code = status
        resp.reason = "OK" if status == 200 else "Not Found"
        resp.headers["Content-Type"] = "application/json"
        resp._content = _json_bytes(
            {
                "method": method,
                "url": url,
                "path": parts.path,
                "query": parts.query,
                "headers": dict(headers or {}),
                "body": json,
            }
        )
        if status >= 400:
            cause = requests.HTTPError()
            cause.response = resp
            raise bazooka_exc.NotFoundError(cause)
        return resp


def _json_bytes(payload):
    return json.dumps(payload).encode()


def _call_with_headers(application, headers):
    """Run call_api with extra headers and return what reached the client."""
    req = webob.Request.blank(mcp.MCP_PATH, method="POST")
    req.authorization = "Bearer token"
    for name, value in headers.items():
        req.headers[name] = value
    req.body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "call_api",
                "arguments": {"method": "GET", "path": "/v1/compute/nodes/"},
            },
        }
    ).encode()
    resp = req.get_response(application)
    text = resp.json_body["result"]["content"][0]["text"]
    return json.loads(text.split("\n", 1)[1])


@pytest.fixture(scope="module")
def application():
    return mcp.McpApplication(user_api_url=USER_API_URL, client=EchoClient())


def post(application, message, authorization="Bearer token"):
    req = webob.Request.blank(mcp.MCP_PATH, method="POST")
    if authorization:
        req.authorization = authorization
    req.body = message if isinstance(message, bytes) else json.dumps(message).encode()
    return req.get_response(application)


def call_tool(application, name, /, **arguments):
    resp = post(
        application,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    result = resp.json_body["result"]
    return result["content"][0]["text"], result["isError"]


class TestTransport:
    def test_serves_nothing_but_the_mcp_path(self, application):
        """This service is the endpoint, not a layer in front of the API."""
        resp = webob.Request.blank("/v1/compute/nodes/").get_response(application)

        assert resp.status_code == 404

    def test_get_is_not_allowed(self, application):
        resp = webob.Request.blank(mcp.MCP_PATH).get_response(application)

        assert resp.status_code == 405

    @pytest.mark.parametrize("authorization", [None, "Basic dXNlcjpwYXNz"])
    def test_requires_bearer_authorization(self, application, authorization):
        resp = post(
            application, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, authorization
        )

        assert resp.status_code == 401
        assert resp.headers["WWW-Authenticate"] == "Bearer"

    def test_parse_error(self, application):
        resp = post(application, b"{")

        assert resp.status_code == 400
        assert resp.json_body["error"]["code"] == mcp.PARSE_ERROR

    def test_batch_is_rejected(self, application):
        resp = post(application, [{"jsonrpc": "2.0", "id": 1, "method": "ping"}])

        assert resp.status_code == 400
        assert resp.json_body["error"]["code"] == mcp.INVALID_REQUEST

    def test_notification_is_accepted(self, application):
        resp = post(
            application, {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )

        assert resp.status_code == 202
        assert not resp.body


class TestProtocol:
    @pytest.mark.parametrize(
        "requested, negotiated",
        [("2025-03-26", "2025-03-26"), ("1999-01-01", "2025-06-18")],
    )
    def test_initialize(self, application, requested, negotiated):
        resp = post(
            application,
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "initialize",
                "params": {"protocolVersion": requested},
            },
        )

        assert resp.content_type == "application/json"
        assert resp.json_body["id"] == 7
        result = resp.json_body["result"]
        assert result["protocolVersion"] == negotiated
        assert "tools" in result["capabilities"]

    def test_tools_list(self, application):
        resp = post(application, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

        names = [tool["name"] for tool in resp.json_body["result"]["tools"]]
        assert names[:3] == ["list_endpoints", "describe_endpoint", "call_api"]
        assert set(names[3:]) == set(mcp.CURATED)

    def test_unknown_method(self, application):
        resp = post(application, {"jsonrpc": "2.0", "id": 1, "method": "nope"})

        assert resp.json_body["error"]["code"] == mcp.METHOD_NOT_FOUND

    def test_unknown_tool(self, application):
        resp = post(
            application,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "nope"},
            },
        )

        assert resp.json_body["error"]["code"] == mcp.INVALID_PARAMS

    @pytest.mark.parametrize(
        "method, params",
        [
            ("initialize", []),
            ("tools/call", "call_api"),
            ("tools/call", {"name": ["call_api"]}),
            ("tools/call", {"name": "call_api", "arguments": []}),
        ],
    )
    def test_malformed_params(self, application, method, params):
        resp = post(
            application,
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        )

        assert resp.status_code == 200
        assert resp.json_body["error"]["code"] == mcp.INVALID_PARAMS

    @pytest.mark.parametrize(
        "name, arguments, message",
        [
            ("call_api", {}, "Missing argument: method"),
            ("call_api", {"method": "GET"}, "Missing argument: path"),
            ("list_endpoints", {"search": None}, "search must be of type string"),
            ("list_endpoints", {"filter": "x"}, "Unknown arguments: filter"),
            (
                "describe_endpoint",
                {"method": 1, "path": "/v1/"},
                "method must be of type string",
            ),
            (
                "call_api",
                {"method": "GET", "path": "/v1/", "query": "a=b"},
                "query must be of type object",
            ),
        ],
    )
    def test_invalid_arguments_are_a_tool_error(
        self, application, name, arguments, message
    ):
        text, is_error = call_tool(application, name, **arguments)

        assert is_error
        assert message in text


class TestTools:
    def test_list_endpoints_filters(self, application):
        text, is_error = call_tool(
            application, "list_endpoints", search="Compute/Nodes"
        )

        assert not is_error
        lines = text.splitlines()
        assert "GET /v1/compute/nodes/ - Get Nodes" in lines
        assert all("/v1/compute/nodes/" in line for line in lines)

    def test_list_endpoints_no_match(self, application):
        text, _ = call_tool(application, "list_endpoints", search="no-such-thing")

        assert text == "No endpoints match."

    def test_describe_endpoint_resolves_refs(self, application):
        text, is_error = call_tool(
            application, "describe_endpoint", method="post", path="/v1/compute/nodes/"
        )

        assert not is_error
        assert "$ref" not in text
        schema = json.loads(text)["responses"]["201"]["content"]["application/json"]
        assert "properties" in schema["schema"]

    def test_describe_unknown_endpoint(self, application):
        text, is_error = call_tool(
            application, "describe_endpoint", method="GET", path="/v1/nope/"
        )

        assert is_error
        assert "list_endpoints" in text

    def test_call_api_calls_the_user_api(self, application):
        text, is_error = call_tool(
            application,
            "call_api",
            method="post",
            path="/v1/compute/nodes/",
            query={"name": ["a", "b"]},
            body={"name": "vm"},
        )

        assert not is_error
        status, body = text.split("\n", 1)
        assert status == "HTTP 200 OK"
        sent = json.loads(body)
        assert sent["method"] == "POST"
        assert sent["url"].startswith(f"{USER_API_URL}/v1/compute/nodes/?")
        assert sent["query"] == "name=a&name=b"
        assert sent["body"] == {"name": "vm"}
        assert sent["headers"]["Authorization"] == "Bearer token"

    @pytest.mark.parametrize(
        "header",
        [
            # Authorization is covered by test_call_api_calls_the_user_api;
            # it cannot be given an arbitrary value and still get past the gate.
            "X-OTP",
            "X-Firebase-AppCheck",
            "X-Goog-Firebase-AppCheck",
            "X-Captcha",
            "X-Forwarded-For",
        ],
    )
    def test_call_api_forwards_the_headers_the_user_api_trusts(
        self, application, header
    ):
        """Credentials, and the proofs the security rule verifiers read."""
        sent = _call_with_headers(application, {header: "proof"})

        assert sent["headers"][header] == "proof"

    def test_call_api_passes_nothing_else_on(self, application):
        """The forwarded list is the boundary between the two services."""
        sent = _call_with_headers(
            application, {"X-Admin": "smuggled", "Cookie": "session=smuggled"}
        )

        assert "X-Admin" not in sent["headers"]
        assert "Cookie" not in sent["headers"]

    def test_call_api_reports_http_errors(self, application):
        text, is_error = call_tool(
            application, "call_api", method="GET", path="/v1/missing/"
        )

        assert is_error
        assert text.startswith("HTTP 404")

    @pytest.mark.parametrize(
        "method, path",
        [("GET", "http://evil/v1/"), ("GET", "/specifications/"), ("POST", "/v1/mcp")],
    )
    def test_call_api_rejects_paths(self, application, method, path):
        _, is_error = call_tool(application, "call_api", method=method, path=path)

        assert is_error

    def test_call_api_rejects_methods(self, application):
        _, is_error = call_tool(
            application, "call_api", method="TRACE", path="/v1/compute/nodes/"
        )

        assert is_error


NODE_UUID = "11111111-2222-3333-4444-555555555555"
PROJECT_UUID = "66666666-7777-8888-9999-000000000000"

# What the document requires of a node, and the caller can set.
NODE_FIELDS = {"cores": 2, "ram": 1024, "disk_spec": {"size": 10}}


class TestCuratedTools:
    """The per-resource tools generated from the OpenAPI document."""

    def test_every_resource_has_the_five_operations(self):
        for name, _, _ in mcp.RESOURCES:
            assert f"list_{name}s" in mcp.CURATED
            for operation in ("get", "create", "update", "delete"):
                assert f"{operation}_{name}" in mcp.CURATED

    @pytest.mark.parametrize(
        "tool, arguments, method, path, query, body",
        [
            (
                "list_nodes",
                {"status": "ACTIVE"},
                "GET",
                "/v1/compute/nodes/",
                "status=ACTIVE",
                None,
            ),
            (
                "get_node",
                {"uuid": NODE_UUID},
                "GET",
                f"/v1/compute/nodes/{NODE_UUID}",
                "",
                None,
            ),
            (
                "delete_node",
                {"uuid": NODE_UUID},
                "DELETE",
                f"/v1/compute/nodes/{NODE_UUID}",
                "",
                None,
            ),
            (
                "create_node",
                dict(NODE_FIELDS, name="vm", project_id=PROJECT_UUID),
                "POST",
                "/v1/compute/nodes/",
                "",
                dict(NODE_FIELDS, name="vm", project_id=PROJECT_UUID),
            ),
            (
                "update_node",
                dict(NODE_FIELDS, uuid=NODE_UUID, name="vm2"),
                "PUT",
                f"/v1/compute/nodes/{NODE_UUID}",
                "",
                dict(NODE_FIELDS, name="vm2"),
            ),
            (
                "create_secret",
                {"name": "s", "project_id": PROJECT_UUID},
                "POST",
                "/v1/secret/secrets/",
                "",
                {"name": "s", "project_id": PROJECT_UUID},
            ),
        ],
    )
    def test_curated_tool_maps_to_a_request(
        self, application, tool, arguments, method, path, query, body
    ):
        text, is_error = call_tool(application, tool, **arguments)

        assert not is_error
        sent = json.loads(text.split("\n", 1)[1])
        assert sent["method"] == method
        assert sent["url"].startswith(USER_API_URL + path)
        assert sent["query"] == query
        assert sent["body"] == body

    def test_read_only_fields_are_not_arguments(self, application):
        """uuid is set by the API, so a create cannot be asked for one."""
        schema = application._get_curated_tools()["create_node"]["inputSchema"]

        assert "uuid" not in schema["properties"]
        assert "cores" in schema["required"]

    @pytest.mark.parametrize(
        "tool",
        [
            "create_node",
            "create_node_set",
            "create_config",
            "create_value",
            "create_secret",
        ],
    )
    def test_a_create_carries_the_project(self, application, tool):
        """The API refuses these without one, so the document asks for it."""
        schema = application._get_curated_tools()[tool]["inputSchema"]

        assert "project_id" in schema["properties"]
        assert "project_id" in schema["required"]

    def test_an_update_does_not_offer_the_project(self, application):
        """It is read-only on an update, where the API keeps what it has."""
        schema = application._get_curated_tools()["update_node"]["inputSchema"]

        assert "project_id" not in schema["properties"]

    def test_a_create_sends_the_project_it_was_given(self, application):
        text, is_error = call_tool(
            application, "create_node", **dict(NODE_FIELDS, project_id=PROJECT_UUID)
        )

        assert not is_error
        sent = json.loads(text.split("\n", 1)[1])
        assert sent["body"]["project_id"] == PROJECT_UUID

    @pytest.mark.parametrize(
        "tool",
        [
            "update_node",
            "update_node_set",
            "update_config",
            "update_value",
            "update_secret",
            "update_element",
            "update_user",
            "update_project",
            "update_organization",
            "update_role",
        ],
    )
    def test_an_update_asks_for_the_uuid_alone(self, application, tool):
        """An update sends only the fields to change, so nothing else is required."""
        schema = application._get_curated_tools()[tool]["inputSchema"]

        assert schema["required"] == ["uuid"]

    def test_an_update_sends_only_the_fields_it_was_given(self, application):
        text, is_error = call_tool(
            application, "update_node", uuid=NODE_UUID, name="vm2"
        )

        assert not is_error
        sent = json.loads(text.split("\n", 1)[1])
        assert sent["body"] == {"name": "vm2"}

    def test_rejects_a_uuid_that_is_not_one(self, application):
        text, is_error = call_tool(application, "get_node", uuid="../../../secret")

        assert is_error
        assert "must be a UUID" in text

    def test_rejects_an_unknown_field(self, application):
        text, is_error = call_tool(application, "create_node", nope="x")

        assert is_error
        assert "Unknown arguments: nope" in text

    def test_rejects_a_wrongly_typed_field(self, application):
        text, is_error = call_tool(
            application,
            "create_node",
            **dict(NODE_FIELDS, cores="many", project_id=PROJECT_UUID),
        )

        assert is_error
        assert "cores must be of type integer" in text
