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

"""MCP (Model Context Protocol) server for the User API.

A stateless implementation of the Streamable HTTP transport: every message is
a single JSON-RPC POST answered with a single JSON body, there are no
sessions and no server-sent events. The official SDK is asyncio-only, while
this API is served by a WSGI server, and the subset used here is small.

The tools do not reimplement the API. `call_api` makes the call against the
User API over HTTP carrying the caller's credentials, so authentication,
security rules and permissions are decided there, exactly as they are for a
direct call. This service holds no credentials of its own and reaches no
database: without a caller's token it can do nothing.

The tool schemas come from the OpenAPI document, built here from the route
tree rather than fetched from the User API. A served document is shaped by
the rights of whoever asked for it, so a fetched one would describe some
particular caller instead of the API, and would describe every caller that
way once cached.
"""

import json
import logging
import urllib.parse
import uuid as uuid_module

import bazooka
from bazooka import exceptions as bazooka_exc
import webob
from webob import dec

from exordos_core import version
from exordos_core.common import openapi

LOG = logging.getLogger(__name__)

MCP_PATH = "/v1/mcp"

# A call is one request the caller is waiting on, so it fails rather than
# holds the worker: this service serves one request at a time per worker.
CALL_TIMEOUT = 60

# Newest first; the first one is offered when the client asks for another.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602

# What a call carries over from the MCP request: the caller's credentials,
# the proofs the security rule verifiers look for, and what the User API
# derives the caller's address and its own URL from. Nothing else is passed
# on -- this list is the boundary between the two services, so a header the
# User API trusts has to be named here to cross it.
FORWARDED_HEADERS = (
    "Authorization",
    "X-OTP",
    "X-Firebase-AppCheck",
    "X-Goog-Firebase-AppCheck",
    "X-Captcha",
    "X-Forwarded-For",
    "X-Forwarded-Host",
    "X-Forwarded-Port",
    "X-Forwarded-Prefix",
    "X-Forwarded-Proto",
    "X-Real-IP",
)

INSTRUCTIONS = (
    "Tools for the Exordos Core User API. The resources you will reach for "
    "most have their own tools, named <verb>_<resource>, with the fields "
    "spelled out. Anything else: find an endpoint with list_endpoints, read "
    "its parameters and body schema with describe_endpoint, then call it "
    "with call_api. Calls run with your credentials and permissions."
)

# Resources that get their own tools, as (name, collection, path parameter).
# Every one of these is a plain REST collection: GET and POST on the
# collection, GET, PUT and DELETE on the item.
RESOURCES = (
    ("node", "/v1/compute/nodes/", "NodeUuid"),
    ("node_set", "/v1/compute/sets/", "NodeSetUuid"),
    ("config", "/v1/config/configs/", "ConfigUuid"),
    ("value", "/v1/vs/values/", "ValueUuid"),
    ("secret", "/v1/secret/secrets/", "SecretUuid"),
    ("element", "/v1/em/elements/", "ElementUuid"),
    ("user", "/v1/iam/users/", "UserUuid"),
    ("project", "/v1/iam/projects/", "ProjectUuid"),
    ("organization", "/v1/iam/organizations/", "OrganizationUuid"),
    ("role", "/v1/iam/roles/", "RoleUuid"),
)

OPERATIONS = ("list", "get", "create", "update", "delete")

# Tool name -> (operation, collection, path parameter). The schemas come from
# the OpenAPI document and are built with it; only this part is static.
CURATED = {
    (f"list_{name}s" if operation == "list" else f"{operation}_{name}"): (
        operation,
        collection,
        parameter,
    )
    for name, collection, parameter in RESOURCES
    for operation in OPERATIONS
}

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

TOOLS_BY_NAME = {tool["name"]: tool for tool in TOOLS}

JSON_TYPES = {
    "string": str,
    "object": dict,
    "array": list,
    "boolean": bool,
    "integer": int,
    "number": (int, float),
}


class ToolError(Exception):
    """A tool failed in a way the model should read and react to."""


class McpApplication:
    """The MCP endpoint, calling the User API at `user_api_url`.

    `user_api_url` is the base the User API answers `/v1/...` under, and
    should be the address callers themselves would use: the User API builds
    absolute URLs from the host it is asked on, so an internal address there
    puts an internal address in what it returns.
    """

    def __init__(self, user_api_url, client=None):
        self.user_api_url = user_api_url.rstrip("/")
        self.client = client or bazooka.Client(default_timeout=CALL_TIMEOUT)
        self._specification = None
        self._curated_tools = None

    @dec.wsgify
    def __call__(self, req):
        if req.path_info.rstrip("/") != MCP_PATH:
            return webob.Response(status=404)
        if req.method != "POST":
            # No server-initiated stream (GET) and no session to end (DELETE).
            return webob.Response(status=405, headers={"Allow": "POST"})
        if not req.authorization or req.authorization.authtype.lower() != "bearer":
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
        params = message.get("params", {})
        if not isinstance(params, dict):
            return _error(msg_id, INVALID_PARAMS, "params must be an object")

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
            curated = list(self._get_curated_tools().values())
            return _result(msg_id, {"tools": TOOLS + curated})
        if method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str) or not (
                name in TOOLS_BY_NAME or name in CURATED
            ):
                return _error(msg_id, INVALID_PARAMS, f"Unknown tool: {name}")
            arguments = params.get("arguments", {})
            if not isinstance(arguments, dict):
                return _error(msg_id, INVALID_PARAMS, "arguments must be an object")
            try:
                if name in CURATED:
                    _check_arguments(self._get_curated_tools()[name], arguments)
                    text, is_error = self._call_curated(req, name, arguments)
                else:
                    _check_arguments(TOOLS_BY_NAME[name], arguments)
                    handler = getattr(self, f"_tool_{name}")
                    text, is_error = handler(req, **arguments)
            except ToolError as e:
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

    def _get_curated_tools(self):
        """Tool name -> tool; built with the specification, on first use."""
        if self._curated_tools is None:
            self._curated_tools = {
                tool["name"]: tool
                for tool in _build_curated_tools(self._get_specification())
            }
        return self._curated_tools

    def _call_curated(self, req, name, arguments):
        operation, collection, _ = CURATED[name]
        arguments = dict(arguments)
        path = collection
        if operation in ("get", "update", "delete"):
            path += _identifier(arguments.pop("uuid"))
        if operation == "list":
            return self._tool_call_api(req, "GET", path, query=arguments)
        if operation == "get":
            return self._tool_call_api(req, "GET", path)
        if operation == "delete":
            return self._tool_call_api(req, "DELETE", path)
        method = "POST" if operation == "create" else "PUT"
        return self._tool_call_api(req, method, path, body=arguments)

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

        url = self.user_api_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query, doseq=True)
        headers = {"Accept": "application/json"}
        for header in FORWARDED_HEADERS:
            if header in req.headers:
                headers[header] = req.headers[header]

        try:
            resp = self.client.request(method, url, headers=headers, json=body)
        except bazooka_exc.BaseHTTPException as e:
            # The User API refused the call and said why; that answer is the
            # tool's result, not a failure of this service.
            resp = e.cause.response
        except Exception:
            LOG.exception("Calling %s %s failed:", method, path)
            raise ToolError(f"Could not reach the User API: {method} {path}")

        text = f"HTTP {resp.status_code} {resp.reason}"
        if resp.content:
            text += "\n" + resp.text
        return text, resp.status_code >= 400


def _check_arguments(tool, arguments):
    """Hold the arguments to the tool's input schema before calling it.

    A wrongly typed argument is the model's mistake to correct, so it comes
    back as a tool error it can read rather than as a failed request.
    """
    schema = tool["inputSchema"]
    properties = schema["properties"]
    unknown = sorted(set(arguments) - set(properties))
    if unknown:
        raise ToolError(f"Unknown arguments: {', '.join(unknown)}")
    for name in schema.get("required", []):
        if name not in arguments:
            raise ToolError(f"Missing argument: {name}")
    for name, value in arguments.items():
        # A generated property need not say what it holds; then anything goes.
        expected = properties[name].get("type")
        if expected is None:
            continue
        if not isinstance(value, JSON_TYPES[expected]):
            raise ToolError(f"Argument {name} must be of type {expected}")
        # bool is an int in Python, but not in JSON.
        if expected in ("integer", "number") and isinstance(value, bool):
            raise ToolError(f"Argument {name} must be of type {expected}")


def _identifier(value):
    """Check a UUID before it becomes part of a path, so none can escape it."""
    try:
        uuid_module.UUID(value)
    except ValueError:
        raise ToolError(f"uuid must be a UUID: {value}")
    return value


def _build_curated_tools(spec):
    """Build the per-resource tools from the OpenAPI document.

    Deriving the schemas rather than writing them out keeps the tools from
    drifting away from the API they call.
    """
    tools = []
    for tool_name, (operation, collection, parameter) in CURATED.items():
        item = f"{collection.rstrip('/')}/{{{parameter}}}"
        label = tool_name.split("_", 1)[1].replace("_", " ")
        required = []
        if operation == "list":
            properties = _query_properties(spec, collection)
            description = f"List {label}. Narrow the list with the filters."
        else:
            properties, description = {}, ""
            if operation in ("get", "update", "delete"):
                properties["uuid"] = {
                    "type": "string",
                    "format": "uuid",
                    "description": f"UUID of the {label}.",
                }
                required.append("uuid")
            if operation in ("create", "update"):
                path, method = (
                    (collection, "post") if operation == "create" else (item, "put")
                )
                body, body_required = _body_properties(spec, path, method)
                properties.update(body)
                # An update sends only the fields to change, so the body's
                # required fields are required of a create alone; asking for
                # them here would make every change a read-modify-write.
                if operation == "create":
                    required.extend(body_required)
            description = {
                "get": f"Return one {label} by UUID.",
                "create": f"Create a {label}.",
                "update": f"Update a {label}. Pass only the fields to change.",
                "delete": f"Delete a {label}.",
            }[operation]
        tool = {
            "name": tool_name,
            "description": description,
            "inputSchema": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        }
        if operation in ("list", "get"):
            tool["annotations"] = {"readOnlyHint": True}
        elif operation in ("update", "delete"):
            tool["annotations"] = {"destructiveHint": True}
        tools.append(tool)
    return tools


def _query_properties(spec, collection):
    properties = {}
    for parameter in _resolve(
        spec, spec["paths"][collection]["get"].get("parameters", []), ()
    ):
        schema = dict(parameter["schema"])
        # A field is read-only on the resource but still a filter here.
        schema.pop("readOnly", None)
        if parameter.get("description"):
            schema["description"] = parameter["description"]
        properties[parameter["name"]] = schema
    return properties


def _body_properties(spec, path, method):
    body = _resolve(spec, spec["paths"][path][method]["requestBody"], ())
    schema = body["content"]["application/json"]["schema"]
    properties = {
        name: value
        for name, value in schema["properties"].items()
        if not value.get("readOnly")
    }
    # A field the caller cannot set is not an argument. The document leaves
    # project_id writable on a create, where the API insists on having it,
    # and read-only on an update, where the API sets it itself.
    required = [name for name in schema.get("required", []) if name in properties]
    return properties, required


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
