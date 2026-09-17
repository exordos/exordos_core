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

| Инструмент | Назначение |
| --- | --- |
| `list_endpoints` | Возвращает строки `METHOD path - summary`, отфильтрованные по необязательной подстроке `search`. |
| `describe_endpoint` | Возвращает операцию OpenAPI для `method` и шаблона пути, все `$ref` раскрыты. |
| `call_api` | Вызывает `method` для конкретного пути `/v1/...` с необязательными `query` и JSON `body`, возвращает HTTP-статус и тело ответа. |

Список эндпоинтов берется из того же документа OpenAPI, что и
[справочник User API](../openapi/openapi_user.md).

Первый вызов `list_endpoints` или `describe_endpoint` в каждом воркере строит
документ, это занимает несколько секунд.

## Настройка клиента

Получите токен (см. [IAM](../iam/permissions_overview.md)) и укажите адрес в
MCP-клиенте с поддержкой HTTP. Например, для Claude Code:

```bash
claude mcp add --transport http exordos \
    https://<your-domain>/api/core/v1/mcp \
    --header "Authorization: Bearer $TOKEN"
```

Или проверьте эндпоинт через `curl`:

```bash
curl -s https://<your-domain>/api/core/v1/mcp \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "call_api",
                    "arguments": {"method": "GET", "path": "/v1/compute/nodes/"}}}'
```
