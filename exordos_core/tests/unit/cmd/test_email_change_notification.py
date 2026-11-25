#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License").

import pathlib
import uuid

from gcl_sdk.events import clients
from gcl_sdk.events.dm import models
import jinja2
import yaml

from exordos_core.events import payloads


def test_email_change_manifest_and_outbox_payload():
    source = pathlib.Path(__file__).parents[4] / "exordos/manifests"
    manifest = yaml.safe_load(
        jinja2.Environment(loader=jinja2.FileSystemLoader(source))
        .get_template("core_notification_email.yaml.j2")
        .render(version="test")
    )
    resources = manifest["resources"]
    mapping = yaml.safe_load(
        resources["$core.config.configs"]["core_event_type_mapping"]["body"]["content"]
    )
    payload = payloads.EmailChangeEventPayload(
        recipient="next@example.com", code="one-time-code"
    )
    # Resolve through the installed SDK entry point and serialize as the sender does.
    event = models.Event(
        event_type=models.UserEvent(user_id=uuid.uuid4(), uuid=uuid.uuid4()),
        event_data=payload,
    )
    selector = event.properties.properties["event_data"].get_property_type()
    restored = selector.from_simple_type(selector.to_simple_type(payload))
    assert restored.to_simple_dict() == {
        "recipient": "next@example.com",
        "code": "one-time-code",
    }
    event_type = clients.AbstractEventClient.get_event_uuid(
        clients.DummyEventClient(event_type_mapping=mapping),
        payload.KIND,
    )
    assert (
        str(event_type)
        == resources["$exordos_notification.types.email.event_types"]["email_change"][
            "uuid"
        ]
    )
    template = resources["$exordos_notification.types.email.templates"][
        "email_change_template"
    ]["content"]
    assert (
        jinja2.Template(template["recipient"]).render(**restored.to_simple_dict())
        == "next@example.com"
    )
    assert "one-time-code" in jinja2.Template(template["bodies"][0]).render(
        **restored.to_simple_dict()
    )
    assert "has been changed" in jinja2.Template(template["bodies"][0]).render(
        recipient="old@example.com", code=""
    )
    for operation in ("create", "read"):
        permission = resources["$core.iam.permissions"][
            f"core_email_change_{operation}"
        ]
        assert (
            permission["name"]
            == f"exordos_notification.event_type_{event_type}.{operation}"
        )
