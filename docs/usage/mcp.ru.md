---
icon: lucide/bot
---
# MCP-сервер

User API также обслуживает
[Model Context Protocol](https://modelcontextprotocol.io/) по адресу
`/v1/mcp`, поэтому ИИ-агент может управлять платформой с теми же учетными
данными и правами, что и обычный клиент API. На инсталляции адрес —
`https://<your-domain>/api/core/v1/mcp`, локальный сервис отвечает на
`http://127.0.0.1:11010/v1/mcp`.

Реализовано подмножество транспорта Streamable HTTP без состояния: каждое
сообщение JSON-RPC — это один `POST` и один JSON-ответ. Сессий, server-sent
events и пакетных запросов нет.

## Аутентификация

Каждый запрос должен содержать заголовок `Authorization: Bearer <token>` с
токеном доступа IAM; без него сервер отвечает `401`. Инструменты выполняются
от имени владельца токена, поэтому права IAM и правила безопасности
применяются так же, как к прямым вызовам API. Заголовок `X-OTP` тоже
передается.

## Инструменты

### Инструменты ресурсов

Для ресурсов, к которым агенты обращаются чаще всего, есть отдельный
инструмент на каждую операцию: `list_<resources>`, `get_<resource>`,
`create_<resource>`, `update_<resource>` и `delete_<resource>`, где все поля
запроса перечислены во входной схеме инструмента. Это узлы, наборы узлов,
конфигурации, значения, секреты, элементы, пользователи, проекты,
организации и роли.

`get`, `update` и `delete` принимают `uuid` ресурса; `list` принимает фильтры
эндпоинта, включая `q`; `create` и `update` принимают поля самого ресурса.
Поля только для чтения, например `project_id`, аргументами не являются — их
заполняет API.

### Общие инструменты

Все, что не покрыто инструментами ресурсов, доступно через них.

| Инструмент | Назначение |
| --- | --- |
| `list_endpoints` | Возвращает строки `METHOD path - summary`, отфильтрованные по необязательной подстроке `search`. |
| `describe_endpoint` | Возвращает операцию OpenAPI для `method` и шаблона пути, все `$ref` раскрыты. |
| `call_api` | Вызывает `method` для конкретного пути `/v1/...` с необязательными `query` и JSON `body`, возвращает HTTP-статус и тело ответа. |

Список эндпоинтов и схемы инструментов ресурсов берутся из того же документа
OpenAPI, что и [справочник User API](../openapi/openapi_user.md), поэтому они
не могут разойтись с API.

Первый вызов `tools/list`, `list_endpoints` или `describe_endpoint` в каждом
воркере строит документ, это занимает несколько секунд.

## Подключение клиентов

### Токен доступа

Получите токен доступа IAM, как показано в
[инструкции локального развёртывания](local_deployment.md#доступ-через-api);
параметр `ttl` задаёт срок его жизни. Когда токен истекает, вызовы
инструментов завершаются с `401` — выпустите новый токен и обновите его в
клиенте.

В примерах ниже используется адрес `https://<your-domain>/api/core/v1/mcp`.
Если клиент умеет брать токен из окружения, он читается из `EXORDOS_TOKEN`:

```bash
export EXORDOS_TOKEN=<access_token>
```

Клиентам без подстановки переменных окружения токен нужно записать прямо в
файл конфигурации; не делитесь этим файлом.

### Copilot CLI

```bash
copilot mcp add --transport http \
    --header "Authorization: Bearer $EXORDOS_TOKEN" \
    exordos https://<your-domain>/api/core/v1/mcp
```

Команда записывает сервер в `~/.copilot/mcp-config.json`. Также можно
выполнить `/mcp add` внутри сессии, выбрать **HTTP** и указать адрес и
`{"Authorization": "Bearer <token>"}` в поле **HTTP Headers**. Чтобы не
хранить токен в файле, отредактируйте запись вручную:

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

### GitHub Copilot в других IDE

IDE JetBrains, Visual Studio, Eclipse и Xcode используют общий формат:
объект `servers`, заголовок — в `requestInit.headers`. Подстановка
переменных ни в одной из них не документирована, поэтому токен указывается
как есть:

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

Куда добавить:

- **JetBrains**: Copilot Chat в режиме Agent, значок инструментов, **Add MCP
  Tools** — откроется `mcp.json`.
- **Visual Studio** 17.14 и новее: Copilot Chat в режиме Agent, значок
  инструментов, **+**. Укажите адрес и добавьте заголовок `Authorization` в
  диалоге или отредактируйте `%USERPROFILE%\.mcp.json` (для всех решений)
  либо `<solution>\.mcp.json`.
- **Eclipse**: **Preferences > GitHub Copilot > MCP**, вставьте JSON в
  **Server Configurations** и нажмите **Apply**.
- **Xcode**: настройки Copilot for Xcode, вкладка **MCP**, **Edit Config**.

### Приложения Claude

**Claude Code** — добавьте сервер из командной строки:

```bash
claude mcp add --transport http exordos \
    https://<your-domain>/api/core/v1/mcp \
    --header "Authorization: Bearer $EXORDOS_TOKEN"
```

Токен при этом сохраняется в `~/.claude.json` в раскрытом виде. Чтобы
подключить сервер для всего проекта, не публикуя токен, закоммитьте в корень
проекта `.mcp.json`:

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

**Claude Desktop** — `claude_desktop_config.json` запускает только
локальные серверы, поэтому подключайтесь через мост
[`mcp-remote`](https://github.com/geelen/mcp-remote) (нужен Node.js).
Отредактируйте `~/Library/Application Support/Claude/claude_desktop_config.json`
в macOS или `%APPDATA%\Claude\claude_desktop_config.json` в Windows и
перезапустите приложение:

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

Пишите `Authorization:${AUTH_HEADER}` без пробела: некоторые клиенты не
экранируют аргументы.

Если вашей организации доступны заголовки запросов для пользовательских
коннекторов (ограниченная бета), можно вместо этого добавить коннектор в
**Customize > Connectors > Add custom connector** без входа и с заголовком
`authorization` со значением `Bearer <token>`. Такие коннекторы вызываются
из облака Anthropic, поэтому инсталляция должна быть доступна из интернета.

### Codex

```bash
codex mcp add exordos \
    --url https://<your-domain>/api/core/v1/mcp \
    --bearer-token-env-var EXORDOS_TOKEN
```

Команда добавляет в `~/.codex/config.toml`, общий для Codex CLI,
расширения IDE и десктопного приложения, следующее. Codex читает
`EXORDOS_TOKEN` при подключении:

```toml
[mcp_servers.exordos]
url = "https://<your-domain>/api/core/v1/mcp"
bearer_token_env_var = "EXORDOS_TOKEN"
```

### Cursor

Добавьте сервер в `~/.cursor/mcp.json` или в `.cursor/mcp.json` проекта:

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

Добавьте сервер в `~/.config/opencode/opencode.json` или в `opencode.json`
проекта:

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

Добавьте сервер в `~/.codeium/windsurf/mcp_config.json`:

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

Откройте настройки командой `zed: open settings file`
(`~/.config/zed/settings.json`) и добавьте сервер; переменные здесь не
подставляются. Ту же запись можно создать в **Settings > AI > MCP Servers >
Add Remote Server**.

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

Выполните `acli rovodev mcp`, чтобы открыть `~/.rovodev/mcp.json`, и
добавьте сервер; переменные здесь тоже не подставляются. Проверить
подключение можно командой `/mcp` в сессии.

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

Чтобы опробовать инструменты в [MCP Inspector](https://github.com/modelcontextprotocol/inspector),
импортируйте [`server.json`](https://github.com/exordos/exordos_core/blob/master/server.json)
из корня репозитория — описание обоих эндпоинтов в формате MCP Registry.
Inspector запросит домен (или хост) и токен.

### curl

```bash
curl -s https://<your-domain>/api/core/v1/mcp \
    -H "Authorization: Bearer $EXORDOS_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "call_api",
                    "arguments": {"method": "GET", "path": "/v1/compute/nodes/"}}}'
```
