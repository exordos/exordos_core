---
icon: lucide/bot
---
# MCP Server

`ec-mcp-api` serves the
[Model Context Protocol](https://modelcontextprotocol.io/), so an AI agent can
manage the platform with the same credentials and permissions as a direct API
client. On an installation the endpoint is
`https://<your-domain>/api/core/v1/mcp`; the service itself answers at
`http://127.0.0.1:11014/v1/mcp`.

It is a service of its own, next to the User API rather than part of it. A
tool call becomes an ordinary HTTP call to the User API carrying the caller's
credentials, so the User API decides what the call may do, exactly as it does
for a direct client. This service holds no credentials and reaches no
database: without a caller's token it can do nothing.

The endpoint implements the stateless subset of the Streamable HTTP
transport: each JSON-RPC message is one `POST`, answered with one JSON
response. There are no sessions, no server-sent events, and no batching.

## Authentication

Every request needs an `Authorization: Bearer <token>` header with an IAM
access token; without the header the endpoint answers `401`. Tool calls run
as the token's owner, so IAM permissions and security rules apply exactly as
they do to direct API calls. `X-OTP` is passed on as well, along with the
headers the security rule verifiers read (`X-Firebase-AppCheck`,
`X-Goog-Firebase-AppCheck` and `X-Captcha`). Nothing else is passed on.

## Configuration

`user_api_url` is the base the service calls the User API under. Use the
address callers themselves use: the User API builds absolute URLs from the
host it is asked on, so an internal address there puts an internal address in
what it returns.

```ini
[mcp_api]
bind_host = 0.0.0.0
bind_port = 11014
user_api_url = http://127.0.0.1:11010
```

## Tools

### Resource tools

The resources agents reach for most have a tool per operation, named
`list_<resources>`, `get_<resource>`, `create_<resource>`,
`update_<resource>` and `delete_<resource>`, with every field of the request
spelled out in the tool's input schema. The resources are nodes, node sets,
configs, values, secrets, elements, users, projects, organizations and roles.

`get`, `update` and `delete` take the resource `uuid`; `list` takes the
endpoint's filters, including `q`; `create` and `update` take the fields of
the resource itself. Read-only fields, such as `project_id`, are not
arguments — the API fills them in.

### Generic tools

Everything the resource tools do not cover is reachable through these.

| Tool | Purpose |
| --- | --- |
| `list_endpoints` | Lists `METHOD path - summary` lines, filtered by an optional `search` substring. |
| `describe_endpoint` | Returns the OpenAPI operation for `method` and a path template, with all `$ref`s resolved. |
| `call_api` | Calls `method` on a concrete `/v1/...` `path` with optional `query` and JSON `body`, and returns the HTTP status and response body. |

The endpoint list and the resource tools' schemas come from the same OpenAPI
document as the [User API reference](../openapi/openapi_user.md), so they
cannot drift from the API.

The first `tools/list`, `list_endpoints` or `describe_endpoint` call in each
worker builds the document, which takes a few seconds.

## Connecting clients

### Access token

Obtain an IAM access token as shown in
[Local Deployment](local_deployment.md#api-access); the `ttl` parameter sets
how long the token lives. When it expires, the client's tool calls fail
with `401`, so issue a new token and update the client.

The examples below use `https://<your-domain>/api/core/v1/mcp`. Where a
client can read the token from the environment, they take it from
`EXORDOS_TOKEN`:

```bash
export EXORDOS_TOKEN=<access_token>
```

Clients without environment variable substitution need the token written
into their configuration file; keep that file private.

### Copilot CLI

```bash
copilot mcp add --transport http \
    --header "Authorization: Bearer $EXORDOS_TOKEN" \
    exordos https://<your-domain>/api/core/v1/mcp
```

The command writes the server to `~/.copilot/mcp-config.json`. Alternatively,
run `/mcp add` inside a session, choose **HTTP**, and enter the URL and
`{"Authorization": "Bearer <token>"}` as **HTTP Headers**. To keep the token
out of the file, edit the entry by hand:

```json
{
  "mcpServers": {
    "exordos": {
      "type": "http",
      "url": "https://<your-domain>/api/core/v1/mcp",
      "headers": {
        "Authorization": "Bearer ${EXORDOS_TOKEN}"
      },
      "tools": ["*"]
    }
  }
}
```

### GitHub Copilot in other IDEs

JetBrains IDEs, Visual Studio, Eclipse, and Xcode share one format: a
`servers` object with the header under `requestInit.headers`. None of them
documents variable substitution, so the token is written as is:

```json
{
  "servers": {
    "exordos": {
      "type": "http",
      "url": "https://<your-domain>/api/core/v1/mcp",
      "requestInit": {
        "headers": {
          "Authorization": "Bearer <token>"
        }
      }
    }
  }
}
```

Where to put it:

- **JetBrains**: Copilot Chat in Agent mode, the tools icon, **Add MCP
  Tools**; this opens `mcp.json`.
- **Visual Studio** 17.14 or later: Copilot Chat in Agent mode, the tools
  icon, **+**. Fill in the URL and add an `Authorization` header in the
  dialog, or edit `%USERPROFILE%\.mcp.json` (all solutions) or
  `<solution>\.mcp.json`.
- **Eclipse**: **Preferences > GitHub Copilot > MCP**, paste the JSON into
  **Server Configurations**, and click **Apply**.
- **Xcode**: Copilot for Xcode settings, **MCP** tab, **Edit Config**.

### Claude applications

**Claude Code** — add the server from the command line:

```bash
claude mcp add --transport http exordos \
    https://<your-domain>/api/core/v1/mcp \
    --header "Authorization: Bearer $EXORDOS_TOKEN"
```

This stores the expanded token in `~/.claude.json`. To share the server with
a project without sharing the token, commit a `.mcp.json` at the project
root instead:

```json
{
  "mcpServers": {
    "exordos": {
      "type": "http",
      "url": "https://<your-domain>/api/core/v1/mcp",
      "headers": {
        "Authorization": "Bearer ${EXORDOS_TOKEN}"
      }
    }
  }
}
```

**Claude Desktop** — `claude_desktop_config.json` starts local servers
only, so connect through the [`mcp-remote`](https://github.com/geelen/mcp-remote)
bridge (requires Node.js). Edit
`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS or
`%APPDATA%\Claude\claude_desktop_config.json` on Windows, then restart the
application:

```json
{
  "mcpServers": {
    "exordos": {
      "command": "npx",
      "args": [
        "mcp-remote",
        "https://<your-domain>/api/core/v1/mcp",
        "--transport",
        "http-only",
        "--header",
        "Authorization:${AUTH_HEADER}"
      ],
      "env": {
        "AUTH_HEADER": "Bearer <token>"
      }
    }
  }
}
```

Keep `Authorization:${AUTH_HEADER}` without a space: some clients do not
quote arguments.

If your organization has request headers for custom connectors (a limited
beta), you can instead add a custom connector at **Customize > Connectors >
Add custom connector** with no sign-in and an `authorization` header of
`Bearer <token>`. Such connectors are called from Anthropic's cloud, so the
installation must be reachable from the internet.

### Codex

```bash
codex mcp add exordos \
    --url https://<your-domain>/api/core/v1/mcp \
    --bearer-token-env-var EXORDOS_TOKEN
```

This adds the following to `~/.codex/config.toml`, which the Codex CLI, the
IDE extension, and the desktop app share. Codex reads `EXORDOS_TOKEN` when it
connects:

```toml
[mcp_servers.exordos]
url = "https://<your-domain>/api/core/v1/mcp"
bearer_token_env_var = "EXORDOS_TOKEN"
```

### Cursor

Add the server to `~/.cursor/mcp.json`, or to `.cursor/mcp.json` in a
project:

```json
{
  "mcpServers": {
    "exordos": {
      "url": "https://<your-domain>/api/core/v1/mcp",
      "headers": {
        "Authorization": "Bearer ${env:EXORDOS_TOKEN}"
      }
    }
  }
}
```

### OpenCode

Add the server to `~/.config/opencode/opencode.json`, or to `opencode.json`
in a project:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "exordos": {
      "type": "remote",
      "url": "https://<your-domain>/api/core/v1/mcp",
      "enabled": true,
      "headers": {
        "Authorization": "Bearer {env:EXORDOS_TOKEN}"
      }
    }
  }
}
```

### Windsurf

Add the server to `~/.codeium/windsurf/mcp_config.json`:

```json
{
  "mcpServers": {
    "exordos": {
      "serverUrl": "https://<your-domain>/api/core/v1/mcp",
      "headers": {
        "Authorization": "Bearer ${env:EXORDOS_TOKEN}"
      }
    }
  }
}
```

### Zed

Open the settings with `zed: open settings file` (`~/.config/zed/settings.json`)
and add the server; Zed does not substitute variables here. The same entry
can be created from **Settings > AI > MCP Servers > Add Remote Server**.

```json
{
  "context_servers": {
    "exordos": {
      "url": "https://<your-domain>/api/core/v1/mcp",
      "headers": {
        "Authorization": "Bearer <token>"
      }
    }
  }
}
```

### Rovo Dev CLI

Run `acli rovodev mcp` to open `~/.rovodev/mcp.json` and add the server;
variables are not substituted here either. Check it with `/mcp` in a
session.

```json
{
  "mcpServers": {
    "exordos": {
      "url": "https://<your-domain>/api/core/v1/mcp",
      "transport": "http",
      "headers": {
        "Authorization": "Bearer <token>"
      }
    }
  }
}
```

### MCP Inspector

To try the tools in [MCP Inspector](https://github.com/modelcontextprotocol/inspector),
import [`server.json`](https://github.com/exordos/exordos_core/blob/master/server.json)
from the repository root, an MCP Registry description of both endpoints.
The Inspector asks for the domain (or host) and the token.

### curl

```bash
curl -s https://<your-domain>/api/core/v1/mcp \
    -H "Authorization: Bearer $EXORDOS_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "call_api",
                    "arguments": {"method": "GET", "path": "/v1/compute/nodes/"}}}'
```
