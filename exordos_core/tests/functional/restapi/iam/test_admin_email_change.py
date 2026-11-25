#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License").

from concurrent.futures import ThreadPoolExecutor
import datetime
import hashlib

from gcl_iam.tests.functional import clients
from gcl_sdk.audit.dm import models as audit_models
from gcl_sdk.events import clients as event_clients
from gcl_sdk.events.dm import models as event_models
import pytest
import requests

from exordos_core.events import constants as event_c
from exordos_core.user_api.iam import constants as c
from exordos_core.user_api.iam.dm import models


@pytest.fixture
def workspace(
    user_api_client,
    auth_user_admin,
    context_storage,
    monkeypatch,
    default_client_uuid,
    default_client_id,
    default_client_secret,
):
    admin = user_api_client(auth_user_admin)
    data = admin.create_user(
        username="email-target", password="Password123", email="before@example.com"
    )
    user = models.User.objects.get_one(filters={"uuid": data["uuid"]})
    admin.confirm_email(user_uuid=user.uuid, code=str(user.confirmation_code))
    user.provision_personal_workspace()
    auth = clients.GenesisCoreAuth(
        username=user.name,
        password="Password123",
        uuid=str(user.uuid),
        client_uuid=default_client_uuid,
        client_id=default_client_id,
        client_secret=default_client_secret,
    )
    client = user_api_client(auth)
    headers = {"Authorization": f"Bearer {client.authenticate()['access_token']}"}
    admin_headers = {"Authorization": f"Bearer {admin.authenticate()['access_token']}"}
    event_client = event_clients.AsyncEventClient(
        event_type_mapping={
            "EventTypeMapping": {
                event_c.IAM_USER_REGISTRATION_EVENT: "e0fe8070-e59c-4347-8df1-b3f638c06565",
                event_c.IAM_USER_EMAIL_CHANGE_EVENT: "57136d71-5f69-4d8d-bd3c-92a5915c52ef",
            },
        }
    )
    monkeypatch.setitem(
        context_storage._storage[c.STORAGE_KEY_EVENTS_CLIENT], "value", event_client
    )
    return client, data["uuid"], headers, admin_headers


def load(workspace):
    return models.User.objects.get_one(filters={"uuid": workspace[1]})


def post(workspace, action, body, headers=None):
    client, identifier, own_headers, _ = workspace
    return requests.post(
        client.build_resource_uri(
            ["iam/users", identifier, f"actions/{action}/invoke"]
        ),
        json=body,
        headers=own_headers if headers is None else headers,
        timeout=10,
    )


def pending_code(workspace):
    user = load(workspace)
    events = event_models.Event.objects.get_all(order_by={"created_at": "desc"})
    return next(
        e.event_data.code
        for e in events
        if e.event_type.user_id == user.uuid and e.event_data.code
    )


def request(workspace, **kwargs):
    return post(
        workspace,
        "admin_change_email",
        {
            "new_email": "after@example.com",
            "reason": "Support ticket",
            **kwargs,
        },
        headers=workspace[3],
    )


def test_confirm_preserves_workspace_and_consumes_code(workspace):
    user = load(workspace)
    org = models.Organization.get_default(user=user).uuid
    project = models.Project.get_default(user=user).uuid
    user.create_confirmation_code()
    registration_code = str(user.confirmation_code)
    assert request(workspace).status_code == 200
    code = pending_code(workspace)
    user = load(workspace)
    assert user.email == "after@example.com" and not user.email_verified
    assert user.email_change_code_hash == hashlib.sha256(code.encode()).hexdigest()
    # Purpose isolation in both directions.
    assert (
        post(workspace, "confirm_email_change", {"code": registration_code}).status_code
        == 400
    )
    assert post(workspace, "confirm_email", {"code": code}).status_code == 403
    assert (
        post(
            workspace, "reset_password", {"code": code, "new_password": "Different123"}
        ).status_code
        == 403
    )
    assert (
        post(workspace, "confirm_email_change", {"code": code}, headers={}).status_code
        == 200
    )
    user = load(workspace)
    assert user.email == "after@example.com" and user.email_verified
    assert user.pending_email is None and user.email_change_code_hash is None
    assert user.confirmation_code is None
    assert models.Organization.get_default(user=user).uuid == org
    assert models.Project.get_default(user=user).uuid == project
    assert post(workspace, "confirm_email_change", {"code": code}).status_code == 400
    notifications = event_models.Event.objects.get_all()
    assert any(
        e.event_data.recipient == "before@example.com" and not e.event_data.code
        for e in notifications
    )
    body = workspace[0].get_user(str(user.uuid))
    assert not (
        {"pending_email", "email_change_code_hash", "email_change_requested_at"}
        & body.keys()
    )
    for event in audit_models.AuditDeliveryEvent.objects.get_all():
        assert "email_change_code_hash" not in (event.snapshot or {})


@pytest.mark.parametrize("verified", [False, True])
def test_admin_change_and_audit(workspace, verified):
    assert request(workspace).status_code == 200
    stale_code = pending_code(workspace)
    response = post(
        workspace,
        "admin_change_email",
        {
            "new_email": "support@example.com",
            "reason": "Ticket 123",
            "verified": verified,
        },
        headers=workspace[3],
    )
    assert response.status_code == 200, response.text
    user = load(workspace)
    assert user.email == "support@example.com" and user.email_verified == verified
    assert (
        post(workspace, "confirm_email_change", {"code": stale_code}).status_code == 400
    )
    events = audit_models.AuditDeliveryEvent.objects.get_all()
    event = next(
        e
        for e in events
        if (e.snapshot or {}).get("operation") == "admin_change_email"
        and e.snapshot.get("reason") == "Ticket 123"
    )
    assert event.snapshot["reason"] == "Ticket 123"
    assert event.actor_user_uuid is not None
    if not verified:
        assert (
            post(
                workspace, "confirm_email_change", {"code": pending_code(workspace)}
            ).status_code
            == 200
        )
        assert load(workspace).email_verified


@pytest.mark.parametrize("extra", [{"reason": " "}, {"verified": "true"}])
def test_admin_validation(workspace, extra):
    response = post(
        workspace,
        "admin_change_email",
        {
            "new_email": "support@example.com",
            "reason": "Ticket 123",
            **extra,
        },
        headers=workspace[3],
    )
    assert response.status_code == 400
    assert load(workspace).email == "before@example.com"


def test_missing_delivery_rolls_back(workspace, context_storage, monkeypatch):
    monkeypatch.setitem(
        context_storage._storage[c.STORAGE_KEY_EVENTS_CLIENT],
        "value",
        event_clients.DummyEventClient(),
    )
    assert request(workspace).status_code == 503
    assert load(workspace).pending_email is None
    assert (
        post(
            workspace,
            "admin_change_email",
            {
                "new_email": "support@example.com",
                "reason": "Ticket 123",
            },
            headers=workspace[3],
        ).status_code
        == 503
    )
    assert load(workspace).email == "before@example.com"


def test_concurrent_confirmation_only_succeeds_once(workspace):
    assert request(workspace).status_code == 200
    code = pending_code(workspace)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: (
                    post(workspace, "confirm_email_change", {"code": code}).status_code
                ),
                range(2),
            )
        )
    assert sorted(results) == [200, 400]


def test_admin_cannot_mark_verified_without_separate_permission(
    workspace,
    user_api_client,
    auth_test1_user,
):
    support = user_api_client(
        auth_test1_user, permissions=[c.PERMISSION_USER_CHANGE_EMAIL]
    )
    headers = {"Authorization": f"Bearer {support.authenticate()['access_token']}"}
    assert (
        post(
            workspace,
            "admin_change_email",
            {
                "new_email": "support@example.com",
                "reason": "Ticket 123",
                "verified": True,
            },
            headers=headers,
        ).status_code
        == 403
    )
    assert load(workspace).email == "before@example.com"
    assert (
        post(
            workspace,
            "admin_change_email",
            {
                "new_email": "support@example.com",
                "reason": "Ticket 123",
            },
            headers=headers,
        ).status_code
        == 200
    )
    assert not load(workspace).email_verified


@pytest.mark.parametrize("source", ["service", "keycloak"])
def test_managed_accounts_cannot_change_email(workspace, source):
    user = load(workspace)
    if source == "service":
        user.type = "service"
    else:
        user.user_source = models.KeycloakUserSource(
            endpoint="https://idp.example.com",
            realm="test",
            client_id="test",
            client_secret="test",
        )
    user.save()
    assert request(workspace).status_code == 403
    assert (
        post(
            workspace,
            "admin_change_email",
            {
                "new_email": "support@example.com",
                "reason": "Ticket 123",
            },
            headers=workspace[3],
        ).status_code
        == 403
    )


@pytest.mark.parametrize("email", ["invalid", "BEFORE@example.com"])
def test_invalid_or_unchanged_address(workspace, email):
    assert request(workspace, new_email=email).status_code == 400
    assert load(workspace).email == "before@example.com"


def test_code_rotation_expiry_and_resend(workspace):
    assert request(workspace).status_code == 200
    old_code = pending_code(workspace)
    # An administrator can resend for the same unverified address.
    assert request(workspace).status_code == 200
    code = pending_code(workspace)
    assert code != old_code
    assert (
        post(workspace, "confirm_email_change", {"code": old_code}).status_code == 400
    )
    user = load(workspace)
    user.email_change_requested_at -= datetime.timedelta(hours=2)
    user.save()
    assert post(workspace, "confirm_email_change", {"code": code}).status_code == 400
    assert not load(workspace).email_verified


def test_user_and_write_all_cannot_change_email(
    workspace, user_api_client, auth_test1_user
):
    body = {"new_email": "support@example.com", "reason": "Ticket 123"}
    assert post(workspace, "admin_change_email", body).status_code == 403
    client = user_api_client(auth_test1_user, permissions=[c.PERMISSION_USER_WRITE_ALL])
    headers = {"Authorization": f"Bearer {client.authenticate()['access_token']}"}
    assert (
        post(workspace, "admin_change_email", body, headers=headers).status_code == 403
    )
    assert load(workspace).email == "before@example.com"


def test_conflicting_address(workspace, user_api_client, auth_user_admin):
    admin = user_api_client(auth_user_admin)
    admin.create_user(
        username="other-user", password="Password123", email="AFTER@example.com"
    )
    assert request(workspace).status_code == 409
    assert load(workspace).email == "before@example.com"


def test_outbox_failure_rolls_back_email_and_audit(workspace, monkeypatch):
    before = len(audit_models.AuditDeliveryEvent.objects.get_all())

    def fail(*args, **kwargs):
        raise RuntimeError("Outbox unavailable")

    monkeypatch.setattr(event_clients.AsyncEventClient, "send_event", fail)
    assert request(workspace).status_code == 500
    assert load(workspace).email == "before@example.com"
    assert len(audit_models.AuditDeliveryEvent.objects.get_all()) == before
