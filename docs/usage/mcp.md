---
icon: lucide/bot
---
# MCP Server

The User API also serves the
[Model Context Protocol](https://modelcontextprotocol.io/) at `/v1/mcp`, so an
AI agent can manage the platform with the same credentials and permissions as
a direct API client. On an installation the endpoint is
`https://<your-domain>/api/core/v1/mcp`; a local service answers at
`http://127.0.0.1:11010/v1/mcp`.

The endpoint implements the stateless subset of the Streamable HTTP
transport: each JSON-RPC message is one `POST`, answered with one JSON
response. There are no sessions, no server-sent events, and no batching.

## Authentication

Every request needs an `Authorization: Bearer <token>` header with an IAM
access token; without the header the endpoint answers `401`. Tool calls run
as the token's owner, so IAM permissions and security rules apply exactly as
they do to direct API calls. An `X-OTP` header is passed on as well.

## Tools

| Tool | Purpose |
| --- | --- |
| `list_endpoints` | Lists `METHOD path - summary` lines, filtered by an optional `search` substring. |
| `describe_endpoint` | Returns the OpenAPI operation for `method` and a path template, with all `$ref`s resolved. |
| `call_api` | Calls `method` on a concrete `/v1/...` `path` with optional `query` and JSON `body`, and returns the HTTP status and response body. |

The endpoint list comes from the same OpenAPI document as the
[User API reference](../openapi/openapi_user.md).

The first `list_endpoints` or `describe_endpoint` call in each worker builds
the document, which takes a few seconds.

## Client configuration

Obtain a token (see [IAM](../iam/permissions_overview.md)), then point an
HTTP-capable MCP client at the endpoint. For example, with Claude Code:

```bash
claude mcp add --transport http exordos \
    https://<your-domain>/api/core/v1/mcp \
    --header "Authorization: Bearer $TOKEN"
```

To add the server to [MCP Inspector](https://github.com/modelcontextprotocol/inspector),
import [`server.json`](https://github.com/exordos/exordos_core/blob/master/server.json)
from the repository root, an MCP Registry description of both endpoints.
The Inspector asks for the domain (or host) and the token.

Or check the endpoint with `curl`:

```bash
curl -s https://<your-domain>/api/core/v1/mcp \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "call_api",
                    "arguments": {"method": "GET", "path": "/v1/compute/nodes/"}}}'
```
