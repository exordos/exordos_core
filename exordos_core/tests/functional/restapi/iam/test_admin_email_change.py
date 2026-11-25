#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License").

from concurrent.futures import ThreadPoolExecutor

from gcl_iam.tests.functional import clients
from gcl_sdk.audit.dm import models as audit_models
from gcl_sdk.events import clients as event_clients
from gcl_sdk.events.dm import models as event_models
import pytest
import requests
from restalchemy.common import contexts
from restalchemy.storage import exceptions as storage_e

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


def request(workspace, **kwargs):
    return post(
        workspace,
        "admin_change_email",
        {
            "new_email": "after@example.com",
            **kwargs,
        },
        headers=workspace[3],
    )


@pytest.mark.parametrize("event_client", [None, event_clients.DummyEventClient()])
def test_admin_change_does_not_need_delivery(
    workspace, context_storage, monkeypatch, event_client
):
    monkeypatch.setitem(
        context_storage._storage[c.STORAGE_KEY_EVENTS_CLIENT], "value", event_client
    )
    assert request(workspace).status_code == 200
    assert load(workspace).email == "after@example.com"
    assert load(workspace).email_verified
    assert load(workspace).pending_email is None


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
            },
            headers=workspace[3],
        ).status_code
        == 403
    )


@pytest.mark.parametrize("email", ["invalid", "BEFORE@example.com"])
def test_invalid_or_unchanged_address(workspace, email):
    assert request(workspace, new_email=email).status_code == 400
    assert load(workspace).email == "before@example.com"


def test_user_and_write_all_cannot_change_email(
    workspace, user_api_client, auth_test1_user
):
    body = {"new_email": "support@example.com"}
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


def test_admin_change_never_emits_notification(workspace, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("Administrative changes must not send notifications")

    monkeypatch.setattr(models.User, "_email_change_event", fail)
    assert request(workspace).status_code == 200
    assert load(workspace).email == "after@example.com"


def test_admin_change_emits_one_automatic_audit(workspace):
    before = {e.uuid for e in audit_models.AuditDeliveryEvent.objects.get_all()}
    assert request(workspace).status_code == 200
    events = [
        e
        for e in audit_models.AuditDeliveryEvent.objects.get_all()
        if e.uuid not in before
        and (e.snapshot or {}).get("email") == "after@example.com"
    ]
    assert len(events) == 1
    assert events[0].actor_user_uuid is not None
    assert events[0].action == "update"
    assert events[0].resource_uuid == load(workspace).uuid


def test_email_login_is_case_insensitive(
    workspace,
    user_api_client,
    default_client_uuid,
    default_client_id,
    default_client_secret,
):
    assert request(workspace, new_email="MixedCase@example.com").status_code == 200

    def authenticate(email):
        auth = clients.GenesisCoreAuth(
            username="",
            password="Password123",
            grant_type="email+password",
            email=email,
            client_uuid=default_client_uuid,
            client_id=default_client_id,
            client_secret=default_client_secret,
        )
        assert user_api_client(auth).authenticate()["access_token"]

    authenticate("MIXEDCASE@EXAMPLE.COM")
    user = load(workspace)
    user.email = "LegacyCase@example.com"
    user.save()
    authenticate("legacycase@example.com")


def test_concurrent_address_claim_returns_conflict(
    workspace, user_api_client, auth_user_admin, monkeypatch, context_storage
):
    import threading

    admin = user_api_client(auth_user_admin)
    other = admin.create_user(
        username="other", password="Password123", email="other@example.com"
    )
    user = models.User.objects.get_one(filters={"uuid": other["uuid"]})
    admin.confirm_email(user_uuid=user.uuid, code=str(user.confirmation_code))
    second = (workspace[0], other["uuid"], workspace[2], workspace[3])
    barrier = threading.Barrier(2)
    original = models.User._validate_email_change

    def validate(self, email):
        result = original(self, email)
        barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(models.User, "_validate_email_change", validate)

    def change(w):
        try:
            with (
                contexts.ContextWithStorage(
                    context_storage=context_storage
                ).context_manager() as ctx,
                ctx.session_manager(),
            ):
                target = models.User.objects.get_one(
                    filters={"uuid": w[1]}, locked=True
                )
                target.admin_change_email("after@example.com")
            return 200
        except storage_e.ConflictRecords as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(change, [workspace, second]))
    assert sorted(statuses) == [200, 409]
    assert len(models.User.find_by_email("AFTER@example.com")) == 1


def test_registration_code_is_rechecked_after_admin_change(
    workspace, monkeypatch, context_storage
):
    import threading

    from exordos_core.user_api.iam.api import controllers

    user = load(workspace)
    user.create_confirmation_code()
    code = str(user.confirmation_code)
    entered = threading.Event()
    resume = threading.Event()
    original = controllers.UserController._lock_email_change_user

    def lock(self, resource):
        if not entered.is_set():
            entered.set()
            assert resume.wait(timeout=5)
        return original(self, resource)

    monkeypatch.setattr(controllers.UserController, "_lock_email_change_user", lock)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(post, workspace, "confirm_email", {"code": code})
        try:
            assert entered.wait(timeout=5)
            with (
                contexts.ContextWithStorage(
                    context_storage=context_storage
                ).context_manager() as ctx,
                ctx.session_manager(),
            ):
                target = models.User.objects.get_one(
                    filters={"uuid": workspace[1]}, locked=True
                )
                target.admin_change_email("after@example.com")
        finally:
            resume.set()
        assert future.result(timeout=5).status_code == 403
    assert load(workspace).email == "after@example.com"
    assert load(workspace).email_verified


def test_admin_change_verifies_immediately_without_code(workspace):
    user = load(workspace)
    organization = models.Organization.get_default(user=user).uuid
    assert request(workspace).status_code == 200
    user = load(workspace)
    assert user.email == "after@example.com" and user.email_verified
    assert user.pending_email is None and user.email_change_code_hash is None
    assert models.Organization.get_default(user=user).uuid == organization
    events = event_models.Event.objects.get_all()
    assert events == []
