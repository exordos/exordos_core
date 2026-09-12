---
icon: lucide/lock
---
# Secrets

Secrets are a part of the Secret Manager service. A secret holds an opaque value the platform does not
interpret: an API token, a license key, a connection string, anything an element needs but should not
carry in its manifest.

Unlike passwords, a secret has no generation method — the value is always supplied by the user, either
as the `value` itself or as a `default_value` a manifest ships with.

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
- **value** - the opaque value. Optional, up to 10240 characters.
- **default_value** - the value to fall back on while `value` is unset. Optional, up to 10240
  characters.

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

## The default value

A manifest author rarely knows the real token an installation will use, but often knows one that works
— a sandbox key, an empty database password, a self-signed licence. That goes into `default_value`:

```yaml
resources:
  $core.secret.secrets:
    grafana_api_token:
      name: "grafana-api-token"
      project_id: "12345678-c625-4fee-81d5-f691897b8142"
      constructor:
        kind: plain
      default_value: "glsa_SANDBOX_TOKEN"
```

The secret behaves as if that were its value: it is what the data plane stores and what an element
reading `:value` renders. Setting `value` through the API takes over from the default, and clearing it
(`{"value": null}`) falls back to the default again. Since the manifest keeps owning `default_value`
and nothing else touches `value`, upgrading the element can change the default without overwriting the
value an operator set.

## A secret with no value blocks its consumers

A secret with neither a `value` nor a `default_value` has nothing to deliver. It stays in the `NEW`
status, nothing reaches the data plane, and an element that renders `$core.secret.secrets.$x:value`
cannot build its target state — it waits, logging:

```text
Target state is not available for resource <resource> by reason: 'value'
```

This is deliberate: an element that needs a secret nobody has supplied yet should stall rather than
deploy with an empty one. Set the value and the next reconciliation cycle picks it up on its own.

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
      default_value: "glsa_XXXXXXXXXXXXXXXX"

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
| `NEW` | The secret was created or updated and is not delivered yet, or it has no value to deliver. |
| `IN_PROGRESS` | The secret is being delivered to the data plane. |
| `ACTIVE` | The value is in place on the data plane. |
| `ERROR` | Delivery failed. |
