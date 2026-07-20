---
icon: lucide/badge-check
---
# Сертификаты

Сертификаты являются частью сервиса Secret Manager. Сервис позволяет выпускать и управлять сертификатами, хранить их в указанном хранилище и использовать для различных целей.

Текущая реализация поддерживает только метод `dns_core` (провайдер) для выпуска и управления сертификатами. Этот метод предполагает DNS-запросы через Core DNS, который доступен из интернета.

Примеры:

```bash
curl --location 'http://10.20.0.2:11010/v1/secret/certificates/' \
--header 'Content-Type: application/json' \
--header 'Authorization: Bearer MY_TOKEN' \
--data-raw '{
    "name": "my-cert",
    "project_id": "00000000-0000-0000-0000-000000000000",
    "method": {
        "kind": "dns_core"
    },
    "constructor": {
        "kind": "plain"
    },
    "email": "user@exordos.com",
    "domains": ["test0.cdns.exordos.com"]
}'
```

Основные поля:

- **name** — имя сертификата.
- **project_id** — проект, к которому относится сертификат.
- **method** — метод (провайдер) для выпуска и управления сертификатом.
- **constructor** — В контексте сертификатов объект constructor создаёт и хранит сертификат. Значение `plain` означает создание и хранение в открытом формате.
- **email** — email-адрес для сертификата.
- **domains** — список доменов для сертификата.

Также можно указать домены с wildcard.

```bash
curl --location 'http://10.20.0.2:11010/v1/secret/certificates/' \
--header 'Content-Type: application/json' \
--header 'Authorization: Bearer MY_TOKEN' \
--data-raw '{
    "name": "my-cert",
    "project_id": "00000000-0000-0000-0000-000000000000",
    "method": {
        "kind": "dns_core"
    },
    "constructor": {
        "kind": "plain"
    },
    "email": "user@exordos.com",
    "domains": ["*.test1.cdns.exordos.com", "test1.cdns.exordos.com"]
}'
```

## Методы / Провайдеры

Exordos Core поддерживает следующие методы / провайдеры для выпуска и управления сертификатами:

### dns_core

Провайдер `dns_core` позволяет выпускать и управлять сертификатами через Core DNS. Это означает, что сервис Core DNS должен быть доступен из интернета для приёма ACME-запросов. Основная логика взаимодействия с Let's Encrypt реализована в плагине [GCL CertBot](https://github.com/infraguys/gcl_certbot_plugin), смотрите его для получения дополнительной информации, но основные шаги:

- Создание или получение приватного ключа клиента.
- Инициализация клиента с ключом.
- Запрос сертификата для доменов.
- Прохождение DNS-запроса.
- Некоторые финальные подготовительные шаги.
