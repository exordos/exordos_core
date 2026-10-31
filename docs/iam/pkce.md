# PKCE for Core IAM

Core IAM supports optional PKCE with `code_challenge_method=S256` in its
OIDC authorization code flow. Existing clients can continue authorizing and
exchanging codes without PKCE. Support is advertised through
`code_challenge_methods_supported` in OIDC discovery.

## Client flow

1. Generate a cryptographically random `code_verifier` for each login attempt.
   Use 43–128 characters from `A-Z`, `a-z`, `0-9`, `-`, `.`, `_`, `~`.
2. Compute `code_challenge = BASE64URL(SHA256(ASCII(code_verifier)))`, without
   Base64 padding. Keep the verifier locally until the code exchange.
3. Send `code_challenge` and `code_challenge_method=S256` with the existing
   authorization parameters to
   `/v1/iam/idp/{idp_uuid}/actions/authorize/invoke`.
4. Validate the returned `state`, then send `code_verifier` alongside
   `grant_type=authorization_code`, `code` and `redirect_uri` to
   `/v1/iam/clients/{client_uuid}/actions/get_token/invoke`.

Only `S256` is supported. A challenge requires an explicit method; `plain`
and omitted methods are rejected. Challenge values must be 43 Base64url
characters without padding.

For codes issued with PKCE, IAM checks the verifier, client, exact redirect URI,
expiration and completed authorization before returning a token. Invalid
exchanges return `invalid_grant` and do not consume the code. Successful
exchanges consume the code under a database row lock in one transaction.
A verifier sent for a code issued without a challenge is rejected.

PKCE remains optional: there is no per-client enforcement setting in this
implementation. Existing behavior for codes issued without PKCE is preserved.
PKCE does not replace client authentication or `state` validation.

## Deployment

Apply migration `0011-iam-pkce-3e8a1472.py` before starting the updated API.
It adds two nullable columns; existing authorization records remain usable.

Protocol reference: [RFC 7636](https://www.rfc-editor.org/rfc/rfc7636).
