---
title: Administrative email changes
---

Administrators can change the email of a local user without changing their
organization or project membership. Service accounts and externally managed
accounts are not supported.

## Change an address

Call `POST /v1/iam/users/{uuid}/actions/admin_change_email/invoke` with an
authenticated token granting `iam.user.change_email`:

```json
{
  "new_email": "new@example.com",
  "reason": "Support ticket 123",
  "verified": false
}
```

The reason is mandatory and must contain 1–1024 characters. The address changes
immediately. By default it is unverified and a confirmation code is sent to the
new address. A notice is sent to the previous address. Setting `verified: true`
also requires `iam.user.verify_changed_email` and skips confirmation.

The response is HTTP 200. Invalid input returns 400, insufficient permissions or
an unsupported account returns 403, and an occupied address returns 409. Email
uniqueness is case-insensitive. General user write permission does not grant
permission to change an email.

The audit record includes the actor, reason, previous address and new address.
Previously issued registration/reset codes and email-change codes are invalidated.
Existing access tokens are not revoked and retain their original claims until
reissued.

## Confirm the new address

Call `POST /v1/iam/users/{uuid}/actions/confirm_email_change/invoke` with:

```json
{
  "code": "code-from-the-email"
}
```

This action accepts an unauthenticated request. The code expires after one hour
and can be used once. Successful confirmation returns HTTP 200 and marks the
address verified. Invalid, expired or consumed codes return 400.

An administrator can repeat the change with the same unverified address to send
a new code. This invalidates the previous code.

## Deployment

Apply `0016-iam-email-change-132dfbf1.py` after the existing element/manifest tags
migration. Deploy Notification with explicit template recipient support
([Notification PR 130](https://github.com/exordos/exordos_notification/pull/130))
and update the Core notification email manifest to register `IamUserEmailChange`.

Configure the SDK asynchronous event client so notifications use the transactional
outbox. A missing or dummy event client returns HTTP 503 and rolls back the
change. The email contains a code for the confirmation API; it does not depend
on a frontend confirmation page.
