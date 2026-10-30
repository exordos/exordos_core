---
icon: lucide/mail
title: Authenticated notification email
---

Core's existing SDK event sender supports Notification 2.0 authorization. It
reads `[events] exordos_api_token` and sends `Authorization: Bearer ...` for
both event submission and status polling. The user API stores events through
the asynchronous SDK client; `ec-gservice` delivers the queue separately.

Install the optional Core element to enable this integration:

```bash
exordos elements install core_notification_email
```

Its `requirements` pull in `notification_email` from the Notification repository,
which provides the common SMTP transport and requires the base Notification
service and mailaas. Core's element owns its registration and password-reset
event types and letter templates, service user, email-event permissions and
automatically renewed IAM token. It delivers these files to Core:

- `event_type_mapping.yaml`: registration and password-reset event type mappings.
- `exordos_core.d/90-notification-events.conf`: enables the asynchronous client
  and sets the Notification endpoint and project.
- `exordos_core.d/91-notification-token.conf`: sets the managed sender token;
  its file mode is `0600`.

The config targets use Core's exported `core_set` resource through an import,
so the integration follows that installation's node set without a fixed UUID.
Update the base Core element to register this export before installing the
optional email integration.

These paths are relative to `/etc/exordos_core`. Both `ec-user-api` and
`ec-gservice` load the configuration directory through `--config-dir`.
When the token changes, the config agent replaces its file and restarts only
`ec-gservice`; the restarted SDK client loads the replacement token.

Provision and verify the sender token before enabling mandatory authorization
in Notification. Do not put a static token in the base Core configuration or
create another service account for the same sender. Other event producers need
their own IAM credentials and event-type permissions.

For a fresh installation, install `core_notification_email` and the Notification
site configuration. Configure the deployment's noreply address before testing
email. The common transport does not include Core event types, templates or
sender configuration; other producers supply their own integration elements.
Existing combined-element installations
need a validated, data-preserving resource ownership/import migration before
adopting the new element layout; do not remove their current SMTP resources.

This element requires `notification_email` version
[2.0.1](https://github.com/exordos/exordos_notification/releases/tag/2.0.1)
or later. The initial 2.0.0 transport still owns Core resources. Register the
new transport version in the installation's repository before installing this
Core element; its dependency floor excludes the old combined layout.

After configuration, verify a real registration event leaves Core's queue,
passes authenticated submission and status polling, and reaches the intended
mailbox. Also verify that token renewal preserves delivery without restarting
the user API. HTTP acceptance alone does not prove mailbox delivery.
