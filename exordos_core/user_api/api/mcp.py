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

"""MCP (Model Context Protocol) endpoint of the User API.

A stateless implementation of the Streamable HTTP transport: every message is
a single JSON-RPC POST answered with a single JSON body, there are no
sessions and no server-sent events. The official SDK is asyncio-only, while
this API is served by a WSGI server, and the subset used here is small.

The tools do not reimplement the API. `call_api` replays the call through the
whole application with the caller's credentials, so authentication, security
rules and permissions apply exactly as they do to a direct call. That is why
this middleware wraps every other one: the IAM middleware refuses to open a
session inside another one.
"""

import json
import urllib.parse

import webob
from webob import dec

from exordos_core import version
from exordos_core.common import openapi

MCP_PATH = "/v1/mcp"

# Newest first; the first one is offered when the client asks for another.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602

# Headers a replayed call takes from the MCP request: credentials and what
# the API derives the caller's address and public URL from.
FORWARDED_HEADERS = (
    "HTTP_AUTHORIZATION",
    "HTTP_X_OTP",
    "HTTP_X_FORWARDED_FOR",
    "HTTP_X_FORWARDED_HOST",
    "HTTP_X_FORWARDED_PORT",
    "HTTP_X_FORWARDED_PREFIX",
    "HTTP_X_FORWARDED_PROTO",
    "HTTP_X_REAL_IP",
    "REMOTE_ADDR",
)

INSTRUCTIONS = (
    "Tools for the Exordos Core User API. Find an endpoint with "
    "list_endpoints, read its parameters and body schema with "
    "describe_endpoint, then call it with call_api. Calls run with your "
    "credentials and permissions."
)

TOOLS = [
    {
        "name": "list_endpoints",
        "description": (
            "List User API endpoints as 'METHOD path - summary' lines. "
            "The API is large, so pass `search` to narrow the list."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "search": {
                    "type": "string",
                    "description": (
                        "Case-insensitive substring matched against the "
                        "path, summary and tags, e.g. 'compute/nodes'."
                    ),
                },
            },
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "describe_endpoint",
        "description": (
            "Return the OpenAPI operation of an endpoint, with every "
            "$ref resolved: parameters, request body and responses."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "method": {"type": "string", "enum": list(HTTP_METHODS)},
                "path": {
                    "type": "string",
                    "description": (
                        "Path template as list_endpoints prints it, "
                        "e.g. '/v1/compute/nodes/{NodeUuid}'."
                    ),
                },
            },
            "required": ["method", "path"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "call_api",
        "description": (
            "Call a User API endpoint and return the HTTP status and body. "
            "Collection paths end with a slash, e.g. '/v1/compute/nodes/'; "
            "actions are invoked with POST on '.../actions/<name>/invoke'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "method": {"type": "string", "enum": list(HTTP_METHODS)},
                "path": {
                    "type": "string",
                    "description": "Concrete path starting with '/v1/'.",
                },
                "query": {
                    "type": "object",
                    "description": (
                        "Query parameters; a list value repeats the parameter."
                    ),
                },
                "body": {
                    "type": "object",
                    "description": "JSON request body.",
                },
            },
            "required": ["method", "path"],
        },
        "annotations": {"destructiveHint": True},
    },
]


class ToolError(Exception):
    """A tool failed in a way the model should read and react to."""


class McpMiddleware:
    def __init__(self, application):
        self.application = application
        self._specification = None

    @dec.wsgify
    def __call__(self, req):
        if req.path_info.rstrip("/") != MCP_PATH:
            return req.get_response(self.application)
        if req.method != "POST":
            # No server-initiated stream (GET) and no session to end (DELETE).
            return webob.Response(status=405, headers={"Allow": "POST"})
        if not req.authorization:
            resp = _json_response(
                _error(None, INVALID_REQUEST, "Authorization required"), status=401
            )
            resp.www_authenticate = "Bearer"
            return resp

        try:
            message = json.loads(req.body)
        except ValueError:
            return _json_response(_error(None, PARSE_ERROR, "Parse error"), 400)
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _json_response(
                _error(None, INVALID_REQUEST, "Invalid request"), status=400
            )

        # Notifications and responses to us need no answer.
        if "method" not in message or "id" not in message:
            return webob.Response(status=202)

        return _json_response(self._dispatch(req, message))

    def _dispatch(self, req, message):
        msg_id = message["id"]
        method = message["method"]
        params = message.get("params") or {}

        if method == "initialize":
            requested = params.get("protocolVersion")
            if requested not in SUPPORTED_PROTOCOL_VERSIONS:
                requested = SUPPORTED_PROTOCOL_VERSIONS[0]
            return _result(
                msg_id,
                {
                    "protocolVersion": requested,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {
                        "name": "exordos-core-user-api",
                        "version": version.version_info,
                    },
                    "instructions": INSTRUCTIONS,
                },
            )
        if method == "ping":
            return _result(msg_id, {})
        if method == "tools/list":
            return _result(msg_id, {"tools": TOOLS})
        if method == "tools/call":
            handler = getattr(self, f"_tool_{params.get('name')}", None)
            if handler is None:
                return _error(
                    msg_id, INVALID_PARAMS, f"Unknown tool: {params.get('name')}"
                )
            try:
                text, is_error = handler(req, **(params.get("arguments") or {}))
            except (ToolError, TypeError) as e:
                text, is_error = str(e), True
            return _result(
                msg_id,
                {"content": [{"type": "text", "text": text}], "isError": is_error},
            )
        return _error(msg_id, METHOD_NOT_FOUND, f"Method not found: {method}")

    def _get_specification(self):
        # Built on first use: it takes a while and most workers never need it.
        if self._specification is None:
            self._specification = openapi.build(openapi.USER_API)
        return self._specification

    def _operations(self):
        for path, item in self._get_specification()["paths"].items():
            for method, operation in item.items():
                if method.upper() in HTTP_METHODS:
                    yield method.upper(), path, operation

    def _tool_list_endpoints(self, req, search=""):
        search = search.lower()
        lines = []
        for method, path, operation in self._operations():
            summary = operation.get("summary", "")
            haystack = " ".join([path, summary, *operation.get("tags", [])])
            if search in haystack.lower():
                lines.append(f"{method} {path} - {summary}")
        return "\n".join(lines) or "No endpoints match.", False

    def _tool_describe_endpoint(self, req, method, path):
        spec = self._get_specification()
        operation = spec["paths"].get(path, {}).get(method.lower())
        if operation is None:
            raise ToolError(
                f"No endpoint {method} {path}; use list_endpoints to find one."
            )
        return json.dumps(_resolve(spec, operation, ()), indent=1), False

    def _tool_call_api(self, req, method, path, query=None, body=None):
        method = method.upper()
        if method not in HTTP_METHODS:
            raise ToolError(f"Unsupported method: {method}")
        if not path.startswith("/v1/") or path.rstrip("/") == MCP_PATH:
            raise ToolError("path must be a User API path starting with '/v1/'.")

        url = path
        if query:
            url += "?" + urllib.parse.urlencode(query, doseq=True)
        environ = {
            key: req.environ[key] for key in FORWARDED_HEADERS if key in req.environ
        }
        sub_req = webob.Request.blank(url, environ=environ, method=method)
        sub_req.accept = "application/json"
        if body is not None:
            sub_req.json_body = body

        resp = sub_req.get_response(self.application)
        text = f"HTTP {resp.status}"
        if resp.body:
            text += "\n" + resp.text
        return text, resp.status_code >= 400


def _json_response(body, status=200):
    return webob.Response(
        status=status, content_type="application/json", json_body=body
    )


def _result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _resolve(spec, node, seen):
    """Inline local $refs; a ref already being expanded is left as is."""
    if isinstance(node, list):
        return [_resolve(spec, item, seen) for item in node]
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        if ref in seen:
            return node
        target = spec
        for part in ref[2:].split("/"):
            target = target[part]
        return _resolve(spec, target, seen + (ref,))
    return {key: _resolve(spec, value, seen) for key, value in node.items()}
