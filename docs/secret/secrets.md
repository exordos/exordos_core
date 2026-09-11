---
icon: lucide/lock
---
# Secrets

Secrets are a part of the Secret Manager service. A secret holds an opaque value the platform does not
interpret: an API token, a license key, a connection string, anything an element needs but should not
carry in its manifest.

Unlike passwords, a secret has no generation method — the value is always supplied by the user.

```bash
curl --location 'http://10.20.0.2:11010/v1/secret/secrets/' \
--header 'Content-Type: application/json' \
--header 'Authorization: Bearer MY_TOKEN' \
--data-raw '{
    "name": "my-secret",
    "project_id": "00000000-0000-0000-0000-000000000000",
    "constructor": {
        "kind": "plain"
    },
    "value": "s3cr3t-api-token"
}'
```

The main fields are:

- **name** - name of the secret.
- **project_id** - it's a project the secret belongs to.
- **constructor** - the constructor object stores the secret. The `plain` means store in the plain
  format.
- **value** - the opaque value. Required, up to 10240 characters.

## The value is write only

The value can be set on create and replaced on update, but it is never read back through the API:

```bash
curl --location 'http://10.20.0.2:11010/v1/secret/secrets/<uuid>' \
--header 'Authorization: Bearer MY_TOKEN'
```

```json
{
    "uuid": "...",
    "name": "my-secret",
    "project_id": "00000000-0000-0000-0000-000000000000",
    "status": "ACTIVE",
    "constructor": {"kind": "plain"}
}
```

There is no `value` field in the response, and no way to ask for one. The only response the value ever
appears in is the reply to the create or update request that supplied it. Rotating a secret is a
regular update:

```bash
curl --request PUT --location 'http://10.20.0.2:11010/v1/secret/secrets/<uuid>' \
--header 'Content-Type: application/json' \
--header 'Authorization: Bearer MY_TOKEN' \
--data-raw '{"value": "new-s3cr3t-api-token"}'
```

The secret goes back to the `NEW` status and the new value is delivered to the data plane.

## Using secrets from a manifest

A secret is consumed by reference. The value is delivered to the node agent, so a manifest reads it
through a link parameter rather than through the API:

```yaml
resources:
  $core.secret.secrets:
    grafana_api_token:
      name: "grafana-api-token"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      constructor:
        kind: plain
      value: "glsa_XXXXXXXXXXXXXXXX"

  $core.config.configs:
    grafana_config:
      name: "grafana-config"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      body: |
        api_token = $core.secret.secrets.$grafana_api_token:value
```

Reference the secret itself with `:uuid` when another resource takes a secret UUID.

## Statuses

| Status | Description |
|---|---|
| `NEW` | The secret was created or updated and is not delivered yet. |
| `IN_PROGRESS` | The secret is being delivered to the data plane. |
| `ACTIVE` | The value is in place on the data plane. |
| `ERROR` | Delivery failed. |
