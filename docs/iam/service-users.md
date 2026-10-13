# Service users and authorization codes

A service user is an IAM identity for automation. Create one with permissions
from your current project, then exchange its authorization code for a token.
The caller needs a project-scoped token with `iam.user.create`,
`iam.service_token.create`, and
permissions covering every requested permission. A specific permission cannot
be delegated as a broader wildcard.

## Create a service user

Call the action on the IAM client that will exchange the code:

```http
POST /v1/iam/clients/<client_uuid>/actions/create_service_code/invoke
Authorization: Bearer <caller_project_token>
Content-Type: application/json

{
  "username": "automation-reader",
  "permissions": ["compute.node.read"]
}
```

Optional service-user fields are `description` (up to 255 characters),
`first_name`, and `last_name` (up to 128 characters each). The description defaults
to an empty string; the names default to `null`.

Optionally include `expiration_at` as an ISO 8601 timestamp with an explicit
timezone, for example `"2026-10-14T08:00:00Z"`. It must be in the future and
no more than 24 hours after creation. When omitted or `null`, the code expires
in ten minutes. This controls the code's lifetime; token lifetimes follow the
IAM client's configuration.

The response contains the new service user's UUID, its one-time code, its
project scope, and the code lifetime in seconds:

```json
{
  "service_user_uuid": "550e8400-e29b-41d4-a716-446655440000",
  "code": "service.<random_value>",
  "expires_in": 600,
  "scope": "project:550e8400-e29b-41d4-a716-446655440001"
}
```

The action creates a dedicated role containing the requested existing IAM
permissions and binds that role to the service user in the caller's project.
The generated email is `service-<uuid>@<caller_email_domain>`, using the domain
of the authenticated caller's email. It does not issue a token or send a
registration email. Only a hash of the code
is stored. Treat the returned code as a credential.

## Exchange the code

Before the code expires, submit a form to the same IAM client:

```http
POST /v1/iam/clients/<client_uuid>/actions/get_token/invoke
Content-Type: application/x-www-form-urlencoded

grant_type=authorization_code&code=service.<random_value>&client_id=<client_id>&client_secret=<client_secret>
```

The client credentials are required for service-user codes. The normal token
response contains an access token and refresh token for the service identity,
scoped to the project selected during creation. No redirect URI is required
for this headless flow.

The code is bound to the receiving client and consumed in the same database
transaction that creates the token. An expired code, a code for another client,
or a previously exchanged code is rejected. Concurrent exchanges can produce
only one token. A service user without a remaining project role binding cannot
exchange its code.

This flow is separate from interactive identity-provider authorization:
`IdpAuthorizationInfo` continues to hold interactive requests and their tokens,
while `ServiceAuthorizationCode` holds pending service-user codes.

## Revoke a pending code

Use your project-scoped caller token to revoke a code before it is exchanged.
The caller needs `iam.service_token.create` in the project where the code was
created. Submit the code to the same IAM client:

```http
POST /v1/iam/clients/<client_uuid>/actions/revoke_service_code/invoke
Authorization: Bearer <caller_project_token>
Content-Type: application/json

{"code": "service.<random_value>"}
```

The response is `{"revoked": true}` when a matching pending code is deleted.
It is `{"revoked": false}` for an unknown, already revoked, or already exchanged
code, or a code belonging to another client or project. Repeating the request
is safe. Expired codes that remain in storage can also be revoked.

Revocation and exchange lock the same record. If revocation completes first,
exchange fails. If exchange completes first, revocation returns `false`.
This action does not revoke an already issued access or refresh token, delete
the service user, or remove its permissions.

## Inspect a pending code

Use your project-scoped caller token to request information without exchanging
or consuming the code. The caller needs `iam.service_token.create` in the
code's project. Submit the code to its IAM client:

```http
POST /v1/iam/clients/<client_uuid>/actions/get_service_code_info/invoke
Authorization: Bearer <caller_project_token>
Content-Type: application/json

{"code": "service.<random_value>"}
```

The response contains `service_user_uuid`, `username`, `client_id`, `project_id`,
`scope`, the current `permissions`, `expires_at` as a UNIX timestamp, and
`active`. An expired code or a service user without a project role binding
has `active: false`. Revoked, exchanged, unknown, and differently scoped codes
are rejected with the same invalid-credentials error. Inspection does not
return tokens or the code itself.

## Refresh service-user tokens

Refresh requests must include the original IAM client's `client_id` and
`client_secret`. Service-user tokens cannot be refreshed through another
client or changed to a different project scope. Omit `scope` to keep the
original scope, or send that exact scope again.

## Logging

The existing API logger can include request and response bodies at DEBUG
level. Those bodies contain authorization codes, client secrets, and tokens.
Protect such logs as credentials; the service-user flow does not change the
logging configuration.
