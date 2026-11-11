# Keycloak password login and required actions

Users with `user_source.kind = KEYCLOAK` are authenticated against Keycloak on
every password login. Core does not accept a locally cached password or update
the local password after a successful Keycloak login. Consequently, a password
reset in Keycloak also prevents the old cached password from being used in Core.
Keycloak availability is required for these logins. Local `IAM` users are unchanged.
Existing Core access and refresh tokens are not revoked by this change.

When an administrator sets a temporary password or assigns `UPDATE_PASSWORD`,
Keycloak requires the user to finish the action in a browser. Direct Access Grants
cannot complete required actions and return `invalid_grant` with the description
`Account is not fully set up`. This response can also indicate other required
actions; Core does not label it specifically as a password reset.

Core's get-token action returns HTTP 400 with this body instead of issuing tokens:

```json
{
  "error": "external_action_required",
  "error_description": "Complete the required actions in Keycloak, then sign in again.",
  "redirect_url": "https://keycloak.example/realms/company/account/"
}
```

A frontend should handle `external_action_required` by offering or performing a
browser navigation to `redirect_url`. The URL uses the configured Keycloak
endpoint and realm, without credentials or tokens. Keycloak's Account Console
must be enabled and the configured endpoint must be browser-accessible.
The user signs in there, completes the required actions, then returns to Core
and signs in again with the new password. There is no automatic callback or
Core session creation from the Account Console in this flow.

Incorrect credentials still produce the normal `invalid_grant` response without
a redirect URL. Client configuration errors, unavailable Keycloak and malformed
responses are not treated as required actions and do not allow a local fallback.
A permanent password replacement does not itself require another password change.

See the [Keycloak Server Administration Guide](https://www.keycloak.org/docs/latest/server_admin/)
for temporary passwords, required actions and the Account Console.
