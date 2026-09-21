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

import pytest
import webob
from webob import dec

from exordos_core.user_api.api import mcp


@dec.wsgify
def echo_app(req):
    """Answer with what the MCP middleware passed down."""
    return webob.Response(
        status=404 if req.path_info == "/v1/missing/" else 200,
        content_type="application/json",
        json_body={
            "method": req.method,
            "path": req.path_info,
            "host_url": req.host_url,
            "query": req.query_string,
            "authorization": req.headers.get("Authorization"),
            "body": req.json_body if req.body else None,
        },
    )


@pytest.fixture(scope="module")
def middleware():
    return mcp.McpMiddleware(echo_app)


def post(middleware, message, authorization="Bearer token"):
    req = webob.Request.blank(
        mcp.MCP_PATH, method="POST", base_url="https://exordos.example.com"
    )
    if authorization:
        req.authorization = authorization
    req.body = message if isinstance(message, bytes) else json.dumps(message).encode()
    return req.get_response(middleware)


def call_tool(middleware, name, /, **arguments):
    resp = post(
        middleware,
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
    def test_other_paths_pass_through(self, middleware):
        resp = webob.Request.blank("/v1/compute/nodes/").get_response(middleware)

        assert resp.json_body["path"] == "/v1/compute/nodes/"

    def test_get_is_not_allowed(self, middleware):
        resp = webob.Request.blank(mcp.MCP_PATH).get_response(middleware)

        assert resp.status_code == 405

    @pytest.mark.parametrize("authorization", [None, "Basic dXNlcjpwYXNz"])
    def test_requires_bearer_authorization(self, middleware, authorization):
        resp = post(
            middleware, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, authorization
        )

        assert resp.status_code == 401
        assert resp.headers["WWW-Authenticate"] == "Bearer"

    def test_parse_error(self, middleware):
        resp = post(middleware, b"{")

        assert resp.status_code == 400
        assert resp.json_body["error"]["code"] == mcp.PARSE_ERROR

    def test_batch_is_rejected(self, middleware):
        resp = post(middleware, [{"jsonrpc": "2.0", "id": 1, "method": "ping"}])

        assert resp.status_code == 400
        assert resp.json_body["error"]["code"] == mcp.INVALID_REQUEST

    def test_notification_is_accepted(self, middleware):
        resp = post(
            middleware, {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )

        assert resp.status_code == 202
        assert not resp.body


class TestProtocol:
    @pytest.mark.parametrize(
        "requested, negotiated",
        [("2025-03-26", "2025-03-26"), ("1999-01-01", "2025-06-18")],
    )
    def test_initialize(self, middleware, requested, negotiated):
        resp = post(
            middleware,
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

    def test_tools_list(self, middleware):
        resp = post(middleware, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

        names = [tool["name"] for tool in resp.json_body["result"]["tools"]]
        assert names[:3] == ["list_endpoints", "describe_endpoint", "call_api"]
        assert set(names[3:]) == set(mcp.CURATED)

    def test_unknown_method(self, middleware):
        resp = post(middleware, {"jsonrpc": "2.0", "id": 1, "method": "nope"})

        assert resp.json_body["error"]["code"] == mcp.METHOD_NOT_FOUND

    def test_unknown_tool(self, middleware):
        resp = post(
            middleware,
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
    def test_malformed_params(self, middleware, method, params):
        resp = post(
            middleware,
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
        self, middleware, name, arguments, message
    ):
        text, is_error = call_tool(middleware, name, **arguments)

        assert is_error
        assert message in text


class TestTools:
    def test_list_endpoints_filters(self, middleware):
        text, is_error = call_tool(middleware, "list_endpoints", search="Compute/Nodes")

        assert not is_error
        lines = text.splitlines()
        assert "GET /v1/compute/nodes/ - Get Nodes" in lines
        assert all("/v1/compute/nodes/" in line for line in lines)

    def test_list_endpoints_no_match(self, middleware):
        text, _ = call_tool(middleware, "list_endpoints", search="no-such-thing")

        assert text == "No endpoints match."

    def test_describe_endpoint_resolves_refs(self, middleware):
        text, is_error = call_tool(
            middleware, "describe_endpoint", method="post", path="/v1/compute/nodes/"
        )

        assert not is_error
        assert "$ref" not in text
        schema = json.loads(text)["responses"]["201"]["content"]["application/json"]
        assert "properties" in schema["schema"]

    def test_describe_unknown_endpoint(self, middleware):
        text, is_error = call_tool(
            middleware, "describe_endpoint", method="GET", path="/v1/nope/"
        )

        assert is_error
        assert "list_endpoints" in text

    def test_call_api_replays_request(self, middleware):
        text, is_error = call_tool(
            middleware,
            "call_api",
            method="post",
            path="/v1/compute/nodes/",
            query={"name": ["a", "b"]},
            body={"name": "vm"},
        )

        assert not is_error
        status, body = text.split("\n", 1)
        assert status == "HTTP 200 OK"
        assert json.loads(body) == {
            "method": "POST",
            "path": "/v1/compute/nodes/",
            "host_url": "https://exordos.example.com",
            "query": "name=a&name=b",
            "authorization": "Bearer token",
            "body": {"name": "vm"},
        }

    @pytest.mark.parametrize(
        "header",
        ["X-OTP", "X-Firebase-AppCheck", "X-Goog-Firebase-AppCheck", "X-Captcha"],
    )
    def test_call_api_forwards_verifier_headers(self, header):
        """The security rule verifiers read these off the replayed request."""

        @dec.wsgify
        def echo_headers(req):
            return webob.Response(
                content_type="application/json",
                json_body={"seen": req.headers.get(header)},
            )

        req = webob.Request.blank(mcp.MCP_PATH, method="POST")
        req.authorization = "Bearer token"
        req.headers[header] = "proof"
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
        resp = req.get_response(mcp.McpMiddleware(echo_headers))

        text = resp.json_body["result"]["content"][0]["text"]
        assert json.loads(text.split("\n", 1)[1]) == {"seen": "proof"}

    def test_call_api_reports_http_errors(self, middleware):
        text, is_error = call_tool(
            middleware, "call_api", method="GET", path="/v1/missing/"
        )

        assert is_error
        assert text.startswith("HTTP 404")

    @pytest.mark.parametrize(
        "method, path",
        [("GET", "http://evil/v1/"), ("GET", "/specifications/"), ("POST", "/v1/mcp")],
    )
    def test_call_api_rejects_paths(self, middleware, method, path):
        _, is_error = call_tool(middleware, "call_api", method=method, path=path)

        assert is_error

    def test_call_api_rejects_methods(self, middleware):
        _, is_error = call_tool(
            middleware, "call_api", method="TRACE", path="/v1/compute/nodes/"
        )

        assert is_error


NODE_UUID = "11111111-2222-3333-4444-555555555555"

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
                dict(NODE_FIELDS, name="vm"),
                "POST",
                "/v1/compute/nodes/",
                "",
                dict(NODE_FIELDS, name="vm"),
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
                {"name": "s"},
                "POST",
                "/v1/secret/secrets/",
                "",
                {"name": "s"},
            ),
        ],
    )
    def test_curated_tool_maps_to_a_request(
        self, middleware, tool, arguments, method, path, query, body
    ):
        text, is_error = call_tool(middleware, tool, **arguments)

        assert not is_error
        replayed = json.loads(text.split("\n", 1)[1])
        assert replayed["method"] == method
        assert replayed["path"] == path
        assert replayed["query"] == query
        assert replayed["body"] == body

    def test_read_only_fields_are_not_arguments(self, middleware):
        """project_id is required in the document but the caller cannot set it."""
        schema = middleware._get_curated_tools()["create_node"]["inputSchema"]

        assert "project_id" not in schema["properties"]
        assert "project_id" not in schema["required"]
        assert "cores" in schema["required"]

    def test_rejects_a_uuid_that_is_not_one(self, middleware):
        text, is_error = call_tool(middleware, "get_node", uuid="../../../secret")

        assert is_error
        assert "must be a UUID" in text

    def test_rejects_an_unknown_field(self, middleware):
        text, is_error = call_tool(middleware, "create_node", nope="x")

        assert is_error
        assert "Unknown arguments: nope" in text

    def test_rejects_a_wrongly_typed_field(self, middleware):
        text, is_error = call_tool(
            middleware, "create_node", **dict(NODE_FIELDS, cores="many")
        )

        assert is_error
        assert "cores must be of type integer" in text
