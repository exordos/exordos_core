---
icon: lucide/key-round
---
# Passwords

Passwords are a part for the Secret Manager service. The service allows to generate and manage passwords, store them in specified storage and use them for different purposes.

The current implementation supports three methods to create passwords: `AUTO_HEX`, `AUTO_URL_SAFE` and `MANUAL`.

Examples:

```bash
curl --location 'http://10.20.0.2:11010/v1/secret/passwords/' \
--header 'Content-Type: application/json' \
--header 'Authorization: Bearer MY_TOKEN' \
--data-raw '{
    "name": "my-password",
    "project_id": "00000000-0000-0000-0000-000000000000",
    "method": "AUTO_HEX",
    "constructor": {
        "kind": "plain"
    },
    "default_length": 32
}'
```

The main fields are:

- **name** - name of the password.
- **project_id** - it's a project the password belongs to.
- **method** - the method to generate or specify the password value.
- **constructor** - in the context of the passwords, the constructor object creates and stores the password. The `plain` means create and store in the plain format.
- **default_length** - the length of the auto-generated password value (default is `32`, maximum is `512`).

## Examples for passwords in manifest

### AUTO_HEX

```yaml
# AUTO_HEX generates a random hex-encoded string via secrets.token_hex()
# After processing, target value will be something like:
#   value: "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
auto_hex_password:
  name: "auto-hex-password"
  project_id: "12345678-c625-4fee-81d5-f691897b8142"
  method: "AUTO_HEX"
  constructor:
    kind: plain
  default_length: 32
```

### AUTO_URL_SAFE

```yaml
# AUTO_URL_SAFE generates a random URL-safe base64 string via secrets.token_urlsafe()
# After processing, target value will be something like:
#   value: "a1b2c3d4-e5f6_a7b8c9d0e1f2a3b4c5d6a1b2c3d4e5f6a7b8"
auto_url_safe_password:
  name: "auto-url-safe-password"
  project_id: "12345678-c625-4fee-81d5-f691897b8142"
  method: "AUTO_URL_SAFE"
  constructor:
    kind: plain
  default_length: 48
```

### MANUAL

```yaml
# MANUAL uses the exact value provided by the user
# After processing, target value will be:
#   value: "my-strong-password-value"
manual_password:
  name: "manual-password"
  project_id: "12345678-c625-4fee-81d5-f691897b8142"
  method: "MANUAL"
  constructor:
    kind: plain
  value: "my-strong-password-value"
```

### LONG_AUTO_HEX

```yaml
# AUTO_HEX with longer length
# After processing, target value will be something like:
#   value: "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6..."
long_auto_hex_password:
  name: "long-auto-hex-password"
  project_id: "12345678-c625-4fee-81d5-f691897b8142"
  method: "AUTO_HEX"
  constructor:
    kind: plain
  default_length: 128
```

## Methods / Generation Strategies

The Exordos Core supports the following methods to generate or provide passwords:

### AUTO_HEX

The `AUTO_HEX` method auto-generates a random hex-encoded string. The password value is generated using Python's `secrets.token_hex()` function. This is the default method.

```bash
curl --location 'http://10.20.0.2:11010/v1/secret/passwords/' \
--header 'Content-Type: application/json' \
--header 'Authorization: Bearer MY_TOKEN' \
--data-raw '{
    "name": "auto-hex-password",
    "project_id": "00000000-0000-0000-0000-000000000000",
    "method": "AUTO_HEX",
    "constructor": {
        "kind": "plain"
    },
    "default_length": 48
}'
```

### AUTO_URL_SAFE

The `AUTO_URL_SAFE` method auto-generates a random URL-safe base64-encoded string. The password value is generated using Python's `secrets.token_urlsafe()` function.

```bash
curl --location 'http://10.20.0.2:11010/v1/secret/passwords/' \
--header 'Content-Type: application/json' \
--header 'Authorization: Bearer MY_TOKEN' \
--data-raw '{
    "name": "auto-url-safe-password",
    "project_id": "00000000-0000-0000-0000-000000000000",
    "method": "AUTO_URL_SAFE",
    "constructor": {
        "kind": "plain"
    },
    "default_length": 32
}'
```

### MANUAL

The `MANUAL` method allows to specify a custom password value explicitly. When using this method, the `value` field is required and must be provided in the request.

```bash
curl --location 'http://10.20.0.2:11010/v1/secret/passwords/' \
--header 'Content-Type: application/json' \
--header 'Authorization: Bearer MY_TOKEN' \
--data-raw '{
    "name": "manual-password",
    "project_id": "00000000-0000-0000-0000-000000000000",
    "method": "MANUAL",
    "constructor": {
        "kind": "plain"
    },
    "value": "my-strong-password-value"
}'
```

## Status Lifecycle

A password goes through the following statuses during its lifecycle:

- **NEW** - the password has been created and is waiting to be processed.
- **IN_PROGRESS** - the password value is being generated or stored by the agent.
- **ACTIVE** - the password has been successfully generated and is ready to use.
- **ERROR** - an error occurred during password processing.

The `status` field is read-only and automatically managed by the system.

## Updating a Password

When updating a password, the status is automatically reset to `NEW` to trigger regeneration of the value.

```bash
curl --location --request PUT 'http://10.20.0.2:11010/v1/secret/passwords/<PASSWORD_UUID>' \
--header 'Content-Type: application/json' \
--header 'Authorization: Bearer MY_TOKEN' \
--data-raw '{
    "default_length": 48
}'
```

## Deleting a Password

```bash
curl --location --request DELETE 'http://10.20.0.2:11010/v1/secret/passwords/<PASSWORD_UUID>' \
--header 'Authorization: Bearer MY_TOKEN'
```
