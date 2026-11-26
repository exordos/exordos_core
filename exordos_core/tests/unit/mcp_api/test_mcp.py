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

from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import json
import threading
import urllib.parse
from unittest import mock

from bazooka import exceptions as bazooka_exc
import pytest
import requests
import webob

from exordos_core.mcp_api.api import mcp

USER_API_URL = "https://exordos.example.com/api/core"


class EchoClient:
    """Answer a call with what the MCP service sent, instead of calling out."""

    def request(self, method, url, headers=None, json=None, allow_redirects=False):
        assert allow_redirects is False
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


def _call_with_headers(application, headers, remote_addr=None):
    """Run call_api with extra headers and return what reached the client."""
    req = webob.Request.blank(mcp.MCP_PATH, method="POST")
    req.authorization = "Bearer token"
    req.remote_addr = remote_addr
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
    def test_request_with_id_but_no_method_is_rejected(self, application):
        resp = post(application, {"jsonrpc": "2.0", "id": 7})

        assert resp.status_code == 400
        assert resp.json_body["id"] == 7
        assert resp.json_body["error"]["code"] == mcp.INVALID_REQUEST

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

    def test_call_api_preserves_the_public_host(self, application):
        sent = _call_with_headers(application, {"Host": "public.example.com"})

        assert sent["headers"]["Host"] == "public.example.com"
        assert sent["url"].startswith(USER_API_URL)

    @pytest.mark.parametrize(
        "headers, expected",
        [
            ({}, {"X-Forwarded-For": "192.0.2.1"}),
            ({"X-Forwarded-For": "192.0.2.2"}, {"X-Forwarded-For": "192.0.2.2"}),
            ({"X-Real-IP": "192.0.2.3"}, {"X-Real-IP": "192.0.2.3"}),
        ],
    )
    def test_call_api_preserves_the_caller_ip(self, application, headers, expected):
        sent = _call_with_headers(application, headers, remote_addr="192.0.2.1")

        forwarded = {
            key: value
            for key, value in sent["headers"].items()
            if key in ("X-Forwarded-For", "X-Real-IP")
        }
        assert forwarded == expected

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

    @pytest.mark.parametrize(
        "tool, field", [("update_secret", "value"), ("update_node", "hostname")]
    )
    def test_an_update_forwards_null_for_a_nullable_field(
        self, application, tool, field
    ):
        text, is_error = call_tool(application, tool, uuid=NODE_UUID, **{field: None})

        assert not is_error
        sent = json.loads(text.split("\n", 1)[1])
        assert sent["method"] == "PUT"
        assert sent["body"] == {field: None}

    def test_rejects_null_for_a_non_nullable_field(self, application):
        text, is_error = call_tool(
            application, "update_node", uuid=NODE_UUID, cores=None
        )

        assert is_error
        assert "cores must be of type integer" in text

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

    def test_schemas_leave_out_documentation(self, application):
        """Examples, titles and string limits are not sent to the model."""

        def keywords(node):
            if isinstance(node, list):
                for item in node:
                    yield from keywords(item)
            elif isinstance(node, dict):
                for key, value in node.items():
                    yield key
                    yield from keywords(value)

        for tool in application._get_curated_tools().values():
            found = set(keywords(tool["inputSchema"]))
            assert not found & mcp.DOC_KEYWORDS, tool["name"]
            assert not [key for key in found if key.startswith("x-")], tool["name"]

    def test_schemas_keep_what_a_request_is_built_from(self, application):
        properties = application._get_curated_tools()["create_node"]["inputSchema"][
            "properties"
        ]

        assert properties["cores"] == {"type": "integer", "minimum": 1, "maximum": 4096}
        assert properties["node_type"]["enum"] == ["HW", "VM"]
        assert properties["node_type"]["default"] == "VM"
        assert properties["hostname"]["nullable"] is True
        assert properties["project_id"]["format"] == "uuid"

    def test_compact_keeps_properties_named_like_keywords(self):
        schema = {
            "type": "object",
            "example": {"pattern": "x"},
            "properties": {"pattern": {"type": "string", "pattern": "^a$"}},
        }

        assert mcp._compact(schema) == {
            "type": "object",
            "properties": {"pattern": {"type": "string"}},
        }

    @pytest.mark.parametrize(
        "tool", ["create_node", "update_node", "create_node_set", "update_node_set"]
    )
    def test_disk_spec_describes_the_root_disk(self, application, tool):
        schema = application._get_curated_tools()[tool]["inputSchema"]

        assert schema["properties"]["disk_spec"] == mcp.FIELD_OVERRIDES["disk_spec"]
        fields = schema["properties"]["disk_spec"]["properties"]
        assert fields["tier"] == {
            "type": "string",
            "enum": ["COLD", "WARM", "HOT"],
            "default": "WARM",
        }
        assert fields["ephemeral"] == {"type": "boolean", "default": True}

    def test_list_filters_have_no_defaults(self, application):
        """A resource's default is not the filter's: status NEW is no filter."""
        for name, tool in application._get_curated_tools().items():
            if name.startswith("list_"):
                for schema in tool["inputSchema"]["properties"].values():
                    assert "default" not in schema, name

    def test_list_filters_leave_out_timestamps(self, application):
        properties = application._get_curated_tools()["list_nodes"]["inputSchema"][
            "properties"
        ]

        assert "created_at" not in properties
        assert "updated_at" not in properties
        assert properties["q"] == mcp.Q_FILTER

    def test_list_users_does_not_filter_by_secrets(self, application):
        properties = application._get_curated_tools()["list_users"]["inputSchema"][
            "properties"
        ]

        assert not set(properties) & mcp.HIDDEN_FILTERS["/v1/iam/users/"]
        assert "email" in properties

    def test_filter_grammar_is_in_the_instructions(self, application):
        result = post(
            application,
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        ).json["result"]

        assert "AIP-160" in result["instructions"]

    def test_compact_drops_empty_defaults_and_any_value(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string", "default": ""},
                "mode": {"type": "string", "default": "0644"},
                "variables": {
                    "type": "object",
                    "additionalProperties": mcp.ANY_VALUE,
                    "default": {},
                },
            },
        }

        assert mcp._compact(schema) == {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "mode": {"type": "string", "default": "0644"},
                "variables": {"type": "object"},
            },
        }


ECOSYSTEM_SPEC = {
    "openapi": "3.0.3",
    "paths": {
        "/v1/realms/": {
            "get": {
                "summary": "List realms",
                "tags": ["realms"],
                "responses": {"200": {"$ref": "#/components/responses/Realms"}},
            },
        },
    },
    "components": {"responses": {"Realms": {"description": "Realm collection"}}},
}


@pytest.fixture
def ecosystem_application():
    specification_client = mock.Mock()
    specification_client.get.return_value.status_code = 200
    specification_client.get.return_value.content = _json_bytes(ECOSYSTEM_SPEC)
    return mcp.McpApplication(
        USER_API_URL,
        client=EchoClient(),
        ecosystem_api_url="http://ecosystem.internal:8080/",
        ecosystem_openapi_url="https://repo.example/1.2.3/openapi.yaml",
        specification_client=specification_client,
    )


class TestEcosystem:
    def test_discovery_caches_only_ecosystem_specification(self, ecosystem_application):
        app = ecosystem_application
        text, failed = call_tool(
            app, "list_endpoints", service="ecosystem", search="realm"
        )
        assert not failed
        assert text == "GET /v1/realms/ - List realms"
        text, failed = call_tool(
            app,
            "describe_endpoint",
            service="ecosystem",
            method="GET",
            path="/v1/realms/",
        )
        assert not failed
        assert json.loads(text)["responses"]["200"] == {
            "description": "Realm collection"
        }
        app.specification_client.get.assert_called_once_with(
            "https://repo.example/1.2.3/openapi.yaml",
            allow_redirects=False,
        )
        assert app._specification is None

    def test_yaml_artifact(self, ecosystem_application):
        app = ecosystem_application
        app.specification_client.get.return_value.content = (
            b"openapi: 3.0.3\npaths:\n  /v1/realms/:\n    get:\n      summary: Realms\n"
        )
        text, failed = call_tool(app, "list_endpoints", service="ecosystem")
        assert not failed
        assert text == "GET /v1/realms/ - Realms"

    @pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE"])
    def test_calls_use_ecosystem_without_loading_spec(
        self, ecosystem_application, method
    ):
        app = ecosystem_application
        text, failed = call_tool(
            app,
            "call_api",
            service="ecosystem",
            method=method,
            path="/v1/realms/",
            query={"name": "my realm"},
            body={"name": "new realm"},
        )
        assert not failed
        sent = json.loads(text.split("\n", 1)[1])
        assert sent["url"] == "http://ecosystem.internal:8080/v1/realms/?name=my+realm"
        assert sent["headers"]["Authorization"] == "Bearer token"
        assert sent["headers"]["X-Forwarded-Prefix"] == "/api/ecosystem"
        assert sent["method"] == method
        assert sent["body"] == {"name": "new realm"}
        app.specification_client.get.assert_not_called()

    def test_default_and_explicit_core_still_address_core(self, ecosystem_application):
        app = ecosystem_application
        for kwargs in ({}, {"service": "core"}):
            text, failed = call_tool(
                app, "call_api", method="GET", path="/v1/realms/", **kwargs
            )
            assert not failed
            sent = json.loads(text.split("\n", 1)[1])
            assert sent["url"] == USER_API_URL + "/v1/realms/"
        app.specification_client.get.assert_not_called()

    @pytest.mark.parametrize(
        "service", ["ecosystem", "unknown", "https://evil.example"]
    )
    @pytest.mark.parametrize(
        "tool, arguments",
        [
            ("list_endpoints", {}),
            ("describe_endpoint", {"method": "GET", "path": "/v1/realms/"}),
            ("call_api", {"method": "GET", "path": "/v1/realms/"}),
        ],
    )
    def test_disabled_or_unknown_service(self, application, service, tool, arguments):
        text, failed = call_tool(application, tool, service=service, **arguments)
        assert failed
        assert "not enabled" in text or "Unknown service" in text

    @pytest.mark.parametrize(
        "content",
        [
            b"<html>error</html>",
            b"[]",
            b"{",
            b"openapi: 2.0\npaths: {}",
            b"openapi: 3.0.3\npaths: []",
            b"openapi: 3.0.3\npaths: { /v1/realms/: [] }",
            b"openapi: 3.0.3\npaths: { /v1/realms/: { get: null } }",
            b"openapi: 3.0.3\npaths: { /v1/realms/: { get: {tags: [1]} } }",
            b"openapi: 3.0.3\npaths: { /v1/realms/: { get: {summary: 1} } }",
            b'openapi: 3.0.3\npaths: { /v1/realms/: { get: {$ref: "#/missing"} } }',
        ],
    )
    def test_invalid_schema_is_not_cached(self, ecosystem_application, content):
        app = ecosystem_application
        app.specification_client.get.return_value.content = content
        text, failed = call_tool(app, "list_endpoints", service="ecosystem")
        assert failed
        assert text == "Could not load the Ecosystem OpenAPI document."
        assert app._ecosystem_specification is None
        app.specification_client.get.return_value.content = _json_bytes(ECOSYSTEM_SPEC)
        assert not call_tool(app, "list_endpoints", service="ecosystem")[1]

    @pytest.mark.parametrize("status", [302, 401, 403, 404, 500])
    def test_schema_http_failure_does_not_affect_core(
        self, ecosystem_application, status
    ):
        app = ecosystem_application
        app.specification_client.get.return_value.status_code = status
        assert call_tool(app, "list_endpoints", service="ecosystem")[1]
        assert not call_tool(app, "call_api", method="GET", path="/v1/compute/nodes/")[
            1
        ]
        assert app._ecosystem_specification is None

    def test_schema_timeout_is_a_tool_error(self, ecosystem_application):
        app = ecosystem_application
        app.specification_client.get.side_effect = requests.Timeout("internal detail")
        text, failed = call_tool(app, "list_endpoints", service="ecosystem")
        assert failed
        assert "internal detail" not in text
        assert app._ecosystem_specification is None

    @pytest.mark.parametrize("status", [301, 401, 403, 500])
    def test_api_error_status_is_preserved(self, ecosystem_application, status):
        app = ecosystem_application
        response = requests.Response()
        response.status_code = status
        response.reason = "Refused"
        response._content = b'{"error":"denied"}'
        app.client = mock.Mock()
        app.client.request.return_value = response
        text, failed = call_tool(
            app, "call_api", service="ecosystem", method="GET", path="/v1/realms/"
        )
        assert failed
        assert text == f'HTTP {status} Refused\n{{"error":"denied"}}'
        assert app.client.request.call_count == 1
        assert app.client.request.call_args.kwargs["allow_redirects"] is False

    def test_api_timeout_is_not_retried(self, ecosystem_application):
        app = ecosystem_application
        app.client = mock.Mock()
        app.client.request.side_effect = requests.Timeout("internal detail")
        text, failed = call_tool(
            app,
            "call_api",
            service="ecosystem",
            method="POST",
            path="/v1/realms/",
            body={},
        )
        assert failed
        assert "internal detail" not in text
        assert app.client.request.call_count == 1

    @pytest.mark.parametrize(
        "path",
        [
            "/v1/../admin",
            "/v1/%2e%2e/admin",
            "/v1/%252e%252e/admin",
            "/v1/realms/?url=https://evil",
            "/v1/realms/#fragment",
            "/v1/%5cadmin",
            "/v1/%6dcp",
            "/v1/realms/%0a",
            "https://evil/v1/realms/",
        ],
    )
    def test_api_cannot_escape_configured_path(self, ecosystem_application, path):
        assert call_tool(
            ecosystem_application,
            "call_api",
            service="ecosystem",
            method="GET",
            path=path,
        )[1]

    def test_instruction_advertises_ecosystem(self, ecosystem_application):
        response = post(
            ecosystem_application, {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
        )
        assert "service='ecosystem'" in response.json_body["result"]["instructions"]

    def test_specification_client_has_bounded_timeout(self):
        with mock.patch.object(mcp.bazooka, "Client") as client:
            mcp.McpApplication(USER_API_URL, client=EchoClient())
        client.assert_called_once_with(default_timeout=mcp.SPEC_TIMEOUT)


@pytest.fixture
def ecosystem_http_server():
    """Exercise actual HTTP serialization against a local API and artifact host."""
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append((self.path, dict(self.headers)))
            status = 200
            if self.path == "/openapi.yaml":
                payload = ECOSYSTEM_SPEC
            elif self.path == "/v1/redirect/":
                status, payload = 302, {}
            elif self.path == "/v1/denied/":
                status, payload = 403, {"error": "denied"}
            else:
                payload = {"path": self.path}
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            if status == 302:
                self.send_header("Location", "/v1/should-not-be-called/")
            self.end_headers()
            self.wfile.write(_json_bytes(payload))

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", received
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_ecosystem_http_flow(ecosystem_http_server):
    base_url, received = ecosystem_http_server
    application = mcp.McpApplication(
        USER_API_URL,
        ecosystem_api_url=base_url,
        ecosystem_openapi_url=base_url + "/openapi.yaml",
    )
    assert not call_tool(application, "list_endpoints", service="ecosystem")[1]
    assert "Authorization" not in received[0][1]

    req = webob.Request.blank(mcp.MCP_PATH, method="POST")
    req.authorization = "Bearer user-project-token"
    req.headers.update(
        {
            "Host": "public.example",
            "X-Forwarded-Prefix": "/api/core",
            "X-Forwarded-Proto": "https",
            "X-OTP": "123456",
            "X-Real-IP": "192.0.2.12",
            "Cookie": "must-not-cross=1",
        }
    )
    req.body = _json_bytes(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "call_api",
                "arguments": {
                    "service": "ecosystem",
                    "method": "GET",
                    "path": "/v1/realms/",
                },
            },
        }
    )
    assert not req.get_response(application).json_body["result"]["isError"]
    headers = received[-1][1]
    assert headers["Authorization"] == "Bearer user-project-token"
    assert headers["Host"] == "public.example"
    assert headers["X-Forwarded-Prefix"] == "/api/ecosystem"
    assert headers["X-Forwarded-Proto"] == "https"
    assert headers["X-OTP"] == "123456"
    assert headers["X-Real-IP"] == "192.0.2.12"
    assert "Cookie" not in headers

    for path, status in [("/v1/denied/", 403), ("/v1/redirect/", 302)]:
        text, failed = call_tool(
            application, "call_api", service="ecosystem", method="GET", path=path
        )
        assert failed
        assert text.startswith(f"HTTP {status}")
    assert [path for path, _ in received] == [
        "/openapi.yaml",
        "/v1/realms/",
        "/v1/denied/",
        "/v1/redirect/",
    ]
