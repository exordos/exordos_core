#    Copyright 2026 Genesis Corporation.
#
#    All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import contextlib
import datetime
import hashlib
from types import SimpleNamespace
from unittest import mock
import uuid

from gcl_iam import exceptions as gcl_exc
from gcl_iam import enforcers
import pytest

from exordos_core.user_api.iam import constants as c
from exordos_core.user_api.iam import exceptions as iam_exc
from exordos_core.user_api.iam.api import controllers
from exordos_core.user_api.iam.dm import models


@pytest.fixture
def controller():
    result = mock.Mock()
    result._service_user_code_project = (
        controllers.ClientsController._service_user_code_project.__get__(result)
    )
    result.get_context.return_value.iam_context.introspection_info.return_value = {
        "permissions": ["compute.node.read", "iam.service_token.create"]
    }
    result.get_context.return_value.get_real_url_with_prefix.return_value = (
        "https://core/v1/"
    )
    result._req.headers = {}
    return result


def test_creation_delegates_current_project_permissions_once(controller):
    client = mock.Mock()
    project = mock.Mock()
    parent_user = mock.Mock(email="owner@example.com")
    with mock.patch.object(
        models.Token,
        "my",
        return_value=SimpleNamespace(project=project, user=parent_user),
    ):
        result = controllers.ClientsController.create_service_code._post(
            controller, client, "automation", ["compute.node.read", "compute.node.read"]
        )
    assert result == client.create_service_user_authorization.return_value
    client.create_service_user_authorization.assert_called_once_with(
        username="automation",
        parent_user=parent_user,
        project=project,
        permission_names=["compute.node.read"],
        expiration_at=None,
        description="",
        first_name=None,
        last_name=None,
    )
    assert controller.enforce.call_args_list == [
        mock.call(c.PERMISSION_SERVICE_TOKEN_CREATE),
        mock.call(
            c.PERMISSION_USER_CREATE, do_raise=True, exc=iam_exc.CanNotCreateUser
        ),
    ]


@pytest.mark.parametrize(
    "permission", ["compute.node.write", "compute.node.*", "*.*.*"]
)
def test_creation_rejects_permissions_the_caller_cannot_delegate(
    controller, permission
):
    client = mock.Mock()
    with mock.patch.object(
        models.Token, "my", return_value=mock.Mock(project=mock.Mock())
    ):
        with pytest.raises(iam_exc.CanNotCreateServiceToken):
            controllers.ClientsController.create_service_code._post(
                controller, client, "automation", [permission]
            )
    client.create_service_user_authorization.assert_not_called()


@pytest.mark.parametrize(
    "held,requested",
    [("compute.*.read", "compute.node.read"), ("*.*.*", "compute.node.*")],
)
def test_creation_allows_permissions_covered_by_caller_wildcards(
    controller, held, requested
):
    controller.get_context.return_value.iam_context.introspection_info.return_value = {
        "permissions": [held]
    }
    client = mock.Mock()
    with mock.patch.object(
        models.Token, "my", return_value=mock.Mock(project=mock.Mock())
    ):
        controllers.ClientsController.create_service_code._post(
            controller, client, "automation", [requested]
        )
    client.create_service_user_authorization.assert_called_once()


@pytest.mark.parametrize(
    "permissions", [[], "compute.node.read", [None], ["node.read"], ["compute..read"]]
)
def test_creation_rejects_invalid_permission_payloads(controller, permissions):
    client = mock.Mock()
    with pytest.raises(controllers.ValidationException):
        controllers.ClientsController.create_service_code._post(
            controller, client, "automation", permissions
        )
    client.create_service_user_authorization.assert_not_called()


def test_creation_requires_a_project(controller):
    client = mock.Mock()
    with mock.patch.object(
        models.Token, "my", return_value=SimpleNamespace(project=None)
    ):
        with pytest.raises(iam_exc.ProjectScopeRequiredError):
            controllers.ClientsController.create_service_code._post(
                controller, client, "automation", ["compute.node.read"]
            )
    client.create_service_user_authorization.assert_not_called()


def test_creation_denied_before_accessing_storage(controller):
    controller.enforce.side_effect = iam_exc.CanNotCreateServiceToken()
    with mock.patch.object(models.Token, "my") as token:
        with pytest.raises(iam_exc.CanNotCreateServiceToken):
            controllers.ClientsController.create_service_code._post(
                controller, mock.Mock(), "automation", ["compute.node.read"]
            )
    token.assert_not_called()


def test_exchange_requires_client_credentials_before_consuming_code(controller):
    client = mock.Mock()
    client.validate_client_creds.side_effect = gcl_exc.ClientAuthenticationError()
    with pytest.raises(gcl_exc.ClientAuthenticationError):
        controllers.ClientsController.get_token._post(
            controller, client, "authorization_code", code="service.code"
        )
    client.get_token_by_authorization_code.assert_not_called()


def test_exchange_supplies_client_credentials_and_request_endpoint(controller):
    client = mock.Mock()
    controllers.ClientsController.get_token._post(
        controller,
        client,
        "authorization_code",
        code="service.code",
        client_id="client",
        client_secret="secret",
    )
    client.validate_client_creds.assert_called_once_with(
        client_id="client", client_secret="secret"
    )
    client.get_token_by_authorization_code.assert_called_once_with(
        code="service.code", redirect_uri=None, root_endpoint="https://core/v1/"
    )


@pytest.fixture
def client_session():
    session = mock.Mock()
    client = mock.Mock(uuid=uuid.uuid4(), client_id="client")
    client._get_service_authorization_code = (
        models.IamClient._get_service_authorization_code.__get__(client)
    )
    client._service_authorization_active = (
        models.IamClient._service_authorization_active.__get__(client)
    )
    client._find_reusable_service_user.return_value = None
    client._get_engine.return_value.session_manager.side_effect = lambda: (
        contextlib.nullcontext(session)
    )
    return client, session


def test_create_stores_only_code_hash_and_no_token(client_session):
    client, session = client_session
    with (
        mock.patch.object(models, "User") as user,
        mock.patch.object(models, "Role") as role,
        mock.patch.object(models, "Permission") as permission,
        mock.patch.object(models, "PermissionBinding") as permission_binding,
        mock.patch.object(models, "RoleBinding") as role_binding,
        mock.patch.object(models, "ServiceAuthorizationCode") as authorization,
        mock.patch.object(models, "Token") as token,
    ):
        result = models.IamClient.create_service_user_authorization(
            client,
            "automation",
            mock.Mock(uuid=uuid.uuid4()),
            ["compute.node.read"],
            parent_user=mock.Mock(email="owner@example.com"),
        )
        assert user.call_args.kwargs["type"] == "service"
        assert user.call_args.kwargs["email"] == (
            f"service-{user.call_args.kwargs['uuid']}@example.com"
        )
        assert (
            authorization.call_args.kwargs["code_hash"]
            == hashlib.sha256(result["code"].encode()).hexdigest()
        )
        assert result["expires_in"] == 600
        assert result["code"].startswith("service.")
        for model in (user, role, permission_binding, role_binding, authorization):
            model.return_value.insert.assert_called_once_with(session=session)
        token.assert_not_called()
        permission.objects.get_one.assert_called_once()


def test_unknown_permission_creates_no_records(client_session):
    client, _ = client_session
    with (
        mock.patch.object(models, "Permission") as permission,
        mock.patch.object(models, "User") as user,
    ):
        permission.objects.get_one.side_effect = LookupError()
        with pytest.raises(LookupError):
            models.IamClient.create_service_user_authorization(
                client,
                "automation",
                mock.Mock(),
                ["unknown.permission.read"],
                parent_user=mock.Mock(email="owner@example.com"),
            )
        user.assert_not_called()


@pytest.mark.parametrize("invalid", ["missing", "expired", "human", "unbound"])
def test_exchange_rejects_invalid_codes_without_creating_tokens(
    client_session, invalid
):
    client, _ = client_session
    authorization = mock.Mock()
    authorization.expiration_at = datetime.datetime.now(
        datetime.timezone.utc
    ) + datetime.timedelta(minutes=1)
    authorization.user.type = "user" if invalid == "human" else "service"
    if invalid == "expired":
        authorization.expiration_at -= datetime.timedelta(minutes=2)
    with (
        mock.patch.object(models, "ServiceAuthorizationCode") as codes,
        mock.patch.object(models, "RoleBinding") as bindings,
        mock.patch.object(models, "Token") as token,
    ):
        codes.objects.get_one_or_none.return_value = (
            None if invalid == "missing" else authorization
        )
        bindings.objects.get_one_or_none.return_value = (
            None if invalid == "unbound" else mock.Mock()
        )
        with pytest.raises(gcl_exc.CredentialsAreInvalidError):
            models.IamClient._redeem_service_authorization_code(
                client, "service.code", "https://core/v1/"
            )
        token.assert_not_called()
        authorization.delete.assert_not_called()


def test_exchange_locks_code_and_consumes_it_in_token_transaction(client_session):
    client, session = client_session
    authorization = mock.Mock()
    authorization.user.type = "service"
    authorization.project.uuid = uuid.uuid4()
    authorization.expiration_at = datetime.datetime.now(
        datetime.timezone.utc
    ) + datetime.timedelta(minutes=1)
    with (
        mock.patch.object(models, "ServiceAuthorizationCode") as codes,
        mock.patch.object(models, "RoleBinding"),
        mock.patch.object(models, "Token") as token,
    ):
        codes.objects.get_one_or_none.side_effect = [authorization, None]
        result = models.IamClient._redeem_service_authorization_code(
            client, "service.code", "https://core/v1/"
        )
        assert result == token.return_value
        token.return_value.insert.assert_called_once_with(session=session)
        authorization.delete.assert_called_once_with(session=session)
        assert codes.objects.get_one_or_none.call_args.kwargs["locked"] is True
        assert codes.objects.get_one_or_none.call_args.kwargs["session"] is session
        filters = codes.objects.get_one_or_none.call_args.kwargs["filters"]
        assert set(filters) == {"code_hash", "iam_client"}
        assert filters["code_hash"].value == hashlib.sha256(b"service.code").hexdigest()
        assert filters["iam_client"].value is client
        assert (
            token.call_args.kwargs["scope"] == f"project:{authorization.project.uuid}"
        )
        with pytest.raises(gcl_exc.CredentialsAreInvalidError):
            models.IamClient._redeem_service_authorization_code(
                client, "service.code", "https://core/v1/"
            )
        assert token.call_count == 1


def test_creation_missing_payload_returns_validation_error(controller):
    client = mock.Mock()
    with pytest.raises(controllers.ValidationException):
        controllers.ClientsController.create_service_code._post(controller, client)
    client.create_service_user_authorization.assert_not_called()


def test_revocation_uses_the_callers_current_project(controller):
    client = mock.Mock()
    client.revoke_service_code.return_value = True
    project = mock.Mock()
    with mock.patch.object(
        models.Token, "my", return_value=SimpleNamespace(project=project)
    ):
        assert controllers.ClientsController.revoke_service_code._post(
            controller, client, code="service.code"
        ) == {"revoked": True}
    client.revoke_service_code.assert_called_once_with("service.code", project)
    controller.enforce.assert_called_once_with(c.PERMISSION_SERVICE_TOKEN_CREATE)


@pytest.mark.parametrize("code", [None, 42, "", "interactive-code"])
def test_revocation_validates_the_code_before_accessing_storage(controller, code):
    client = mock.Mock()
    with mock.patch.object(models.Token, "my") as token:
        with pytest.raises(controllers.ValidationException):
            controllers.ClientsController.revoke_service_code._post(
                controller, client, code
            )
    token.assert_not_called()
    client.revoke_service_code.assert_not_called()


def test_revocation_denied_before_accessing_storage(controller):
    client = mock.Mock()
    controller.enforce.side_effect = iam_exc.CanNotCreateServiceToken()
    with mock.patch.object(models.Token, "my") as token:
        with pytest.raises(iam_exc.CanNotCreateServiceToken):
            controllers.ClientsController.revoke_service_code._post(
                controller, client, "service.code"
            )
    token.assert_not_called()
    client.revoke_service_code.assert_not_called()


def test_revocation_requires_project_scope(controller):
    client = mock.Mock()
    with mock.patch.object(
        models.Token, "my", return_value=SimpleNamespace(project=None)
    ):
        with pytest.raises(iam_exc.ProjectScopeRequiredError):
            controllers.ClientsController.revoke_service_code._post(
                controller, client, "service.code"
            )
    client.revoke_service_code.assert_not_called()


@pytest.mark.parametrize("pending", [True, False])
def test_revocation_locks_only_matching_client_project_and_code(
    client_session, pending
):
    client, session = client_session
    project = mock.Mock()
    authorization = mock.Mock()
    with mock.patch.object(models, "ServiceAuthorizationCode") as codes:
        codes.objects.get_one_or_none.return_value = authorization if pending else None
        result = models.IamClient.revoke_service_code(client, "service.code", project)
        assert result is pending
        query = codes.objects.get_one_or_none.call_args.kwargs
        assert query["locked"] is True
        assert query["session"] is session
        assert set(query["filters"]) == {"code_hash", "iam_client", "project"}
        assert (
            query["filters"]["code_hash"].value
            == hashlib.sha256(b"service.code").hexdigest()
        )
        assert query["filters"]["iam_client"].value is client
        assert query["filters"]["project"].value is project
        if pending:
            authorization.delete.assert_called_once_with(session=session)
        else:
            authorization.delete.assert_not_called()


@pytest.mark.parametrize(
    "held,requested,allowed",
    [
        (["iam.service_token.create", "compute.node.read"], "compute.node.read", False),
        (["iam.user.create", "compute.node.read"], "compute.node.read", False),
        (
            ["iam.user.create", "iam.service_token.create", "compute.node.read"],
            "compute.node.write",
            False,
        ),
        (
            ["iam.user.create", "iam.service_token.create", "compute.node.read"],
            "compute.node.*",
            False,
        ),
        (
            ["iam.user.create", "iam.service_token.create", "compute.node.*"],
            "compute.node.read",
            True,
        ),
        (["*.*.*"], "compute.node.*", True),
    ],
)
def test_creation_checks_real_enforcer_and_permission_coverage(
    controller, held, requested, allowed
):
    controller.enforce.side_effect = enforcers.Enforcer(held).enforce
    controller.get_context.return_value.iam_context.introspection_info.return_value = {
        "permissions": held
    }
    client = mock.Mock()
    with mock.patch.object(
        models.Token,
        "my",
        return_value=SimpleNamespace(project=mock.Mock(), user=mock.Mock()),
    ):
        if allowed:
            controllers.ClientsController.create_service_code._post(
                controller, client, "automation", [requested]
            )
            client.create_service_user_authorization.assert_called_once()
        else:
            with pytest.raises(
                (iam_exc.CanNotCreateServiceToken, iam_exc.CanNotCreateUser)
            ):
                controllers.ClientsController.create_service_code._post(
                    controller, client, "automation", [requested]
                )
            client.create_service_user_authorization.assert_not_called()


@pytest.mark.parametrize(
    "scope,client_matches,credentials_ok,allowed",
    [
        (None, True, True, True),
        ("project:current", True, True, True),
        ("project:other", True, True, False),
        (None, False, True, False),
        (None, True, False, False),
    ],
)
def test_service_refresh_cannot_change_client_or_project(
    client_session, scope, client_matches, credentials_ok, allowed
):
    client, _ = client_session
    token = mock.Mock()
    token.user.type = "service"
    token.iam_client.uuid = client.uuid if client_matches else uuid.uuid4()
    token.scope = "project:current"
    if not credentials_ok:
        client.validate_client_creds.side_effect = gcl_exc.ClientAuthenticationError()
    with (
        mock.patch.object(models.tokens, "RefreshToken"),
        mock.patch.object(models, "Token") as token_model,
    ):
        token_model.objects.get_all.return_value = [token]
        if allowed:
            assert (
                models.IamClient.get_token_by_refresh_token(
                    client, "refresh", scope, "client", "secret"
                )
                is token
            )
            token.refresh.assert_called_once_with(scope=scope)
        else:
            with pytest.raises(
                (gcl_exc.ClientAuthenticationError, gcl_exc.InvalidRefreshTokenError)
            ):
                models.IamClient.get_token_by_refresh_token(
                    client, "refresh", scope, "client", "secret"
                )
            token.refresh.assert_not_called()
        client.validate_client_creds.assert_called_once_with("client", "secret")


def test_human_refresh_does_not_require_new_credentials(client_session):
    client, _ = client_session
    token = mock.Mock()
    token.user.type = "user"
    with (
        mock.patch.object(models.tokens, "RefreshToken"),
        mock.patch.object(models, "Token") as token_model,
    ):
        token_model.objects.get_all.return_value = [token]
        assert models.IamClient.get_token_by_refresh_token(client, "refresh") is token
        client.validate_client_creds.assert_not_called()


@pytest.mark.parametrize("action", ["get_service_code_info", "revoke_service_code"])
def test_code_management_rejects_real_enforcer_denial(controller, action):
    controller.enforce.side_effect = enforcers.Enforcer([]).enforce
    client = mock.Mock()
    with mock.patch.object(models.Token, "my") as token:
        with pytest.raises(iam_exc.CanNotCreateServiceToken):
            getattr(controllers.ClientsController, action)._post(
                controller, client, "service.code"
            )
        token.assert_not_called()
        getattr(client, action).assert_not_called()


def test_code_info_uses_the_callers_project_without_exchanging(controller):
    client = mock.Mock()
    project = mock.Mock()
    with mock.patch.object(
        models.Token, "my", return_value=SimpleNamespace(project=project)
    ):
        assert (
            controllers.ClientsController.get_service_code_info._post(
                controller, client, "service.code"
            )
            == client.get_service_code_info.return_value
        )
    client.get_service_code_info.assert_called_once_with("service.code", project)
    client.get_token_by_authorization_code.assert_not_called()


@pytest.mark.parametrize("state", ["active", "expired", "human", "unbound"])
def test_code_info_returns_metadata_and_does_not_consume_code(client_session, state):
    client, session = client_session
    project = mock.Mock(uuid=uuid.uuid4())
    authorization = mock.Mock()
    authorization.project = project
    authorization.user.uuid = uuid.uuid4()
    authorization.user.name = "automation"
    authorization.user.type = "user" if state == "human" else "service"
    authorization.expiration_at = datetime.datetime.now(
        datetime.timezone.utc
    ) + datetime.timedelta(minutes=-1 if state == "expired" else 1)
    with (
        mock.patch.object(models, "ServiceAuthorizationCode") as codes,
        mock.patch.object(models, "RoleBinding") as bindings,
        mock.patch.object(models, "PermissionFastView") as permissions,
    ):
        codes.objects.get_one_or_none.return_value = authorization
        permissions.objects.get_all.return_value = [
            SimpleNamespace(permission=SimpleNamespace(name="compute.node.read"))
        ]
        bindings.objects.get_one_or_none.return_value = (
            None if state == "unbound" else mock.Mock()
        )
        result = models.IamClient.get_service_code_info(client, "service.code", project)
        assert result["active"] is (state == "active")
        assert result["permissions"] == ["compute.node.read"]
        assert result["project_id"] == str(project.uuid)
        assert result["client_id"] == client.client_id
        assert "code" not in result
        query = codes.objects.get_one_or_none.call_args.kwargs
        assert set(query["filters"]) == {"code_hash", "iam_client", "project"}
        assert (
            query["filters"]["code_hash"].value
            == hashlib.sha256(b"service.code").hexdigest()
        )
        assert query["filters"]["iam_client"].value is client
        assert query["filters"]["project"].value is project
        assert query["session"] is session
        authorization.delete.assert_not_called()


def test_code_info_rejects_unknown_or_differently_scoped_codes(client_session):
    client, _ = client_session
    with mock.patch.object(models, "ServiceAuthorizationCode") as codes:
        codes.objects.get_one_or_none.return_value = None
        with pytest.raises(gcl_exc.CredentialsAreInvalidError):
            models.IamClient.get_service_code_info(client, "service.code", mock.Mock())


@pytest.mark.parametrize(
    "expiration_at",
    [
        "2026-10-13T13:00:00Z",
        "2026-10-13T15:00:00+02:00",
        "2026-10-14T12:00:00Z",
    ],
)
def test_creation_accepts_custom_expiration_in_utc(controller, expiration_at):
    now = datetime.datetime(2026, 10, 13, 12, tzinfo=datetime.timezone.utc)
    expected = datetime.datetime.fromisoformat(
        expiration_at.replace("Z", "+00:00")
    ).astimezone(datetime.timezone.utc)
    client = mock.Mock()
    with (
        mock.patch.object(
            models.Token, "my", return_value=mock.Mock(project=mock.Mock())
        ),
        mock.patch.object(
            controllers.datetime, "datetime", wraps=datetime.datetime
        ) as clock,
    ):
        clock.now.return_value = now
        controllers.ClientsController.create_service_code._post(
            controller, client, "automation", ["compute.node.read"], expiration_at
        )
    assert (
        client.create_service_user_authorization.call_args.kwargs["expiration_at"]
        == expected
    )


@pytest.mark.parametrize(
    "expiration_at",
    [
        "invalid",
        "2026-10-13",
        "2026-10-13T13:00:00",
        "2026-10-13T11:59:59Z",
        "2026-10-13T12:00:00Z",
        "2026-10-14T12:00:01Z",
        123,
        False,
        {},
    ],
)
def test_creation_rejects_invalid_expiration_before_creating_user(
    controller, expiration_at
):
    now = datetime.datetime(2026, 10, 13, 12, tzinfo=datetime.timezone.utc)
    client = mock.Mock()
    with (
        mock.patch.object(
            models.Token, "my", return_value=mock.Mock(project=mock.Mock())
        ),
        mock.patch.object(
            controllers.datetime, "datetime", wraps=datetime.datetime
        ) as clock,
    ):
        clock.now.return_value = now
        with pytest.raises(controllers.ValidationException):
            controllers.ClientsController.create_service_code._post(
                controller, client, "automation", ["compute.node.read"], expiration_at
            )
    client.create_service_user_authorization.assert_not_called()


@pytest.mark.parametrize("lifetime", [600, 86400])
def test_creation_stores_custom_expiration_and_returns_its_lifetime(
    client_session, lifetime
):
    client, _ = client_session
    now = datetime.datetime(2026, 10, 13, 12, tzinfo=datetime.timezone.utc)
    expiration_at = now + datetime.timedelta(seconds=lifetime)
    with (
        mock.patch.object(
            models.datetime, "datetime", wraps=datetime.datetime
        ) as clock,
        mock.patch.object(models, "User"),
        mock.patch.object(models, "Role"),
        mock.patch.object(models, "Permission"),
        mock.patch.object(models, "PermissionBinding"),
        mock.patch.object(models, "RoleBinding"),
        mock.patch.object(models, "ServiceAuthorizationCode") as authorization,
    ):
        clock.now.return_value = now
        result = models.IamClient.create_service_user_authorization(
            client,
            "automation",
            mock.Mock(),
            ["compute.node.read"],
            expiration_at,
            parent_user=mock.Mock(email="owner@example.com"),
        )
    assert authorization.call_args.kwargs["expiration_at"] == expiration_at
    assert result["expires_in"] == lifetime


def test_creation_passes_optional_user_fields(controller):
    client = mock.Mock()
    fields = {"description": "CI automation", "first_name": "Build", "last_name": "Bot"}
    with mock.patch.object(
        models.Token, "my", return_value=mock.Mock(project=mock.Mock())
    ):
        controllers.ClientsController.create_service_code._post(
            controller, client, "automation", ["compute.node.read"], **fields
        )
    for name, value in fields.items():
        assert client.create_service_user_authorization.call_args.kwargs[name] == value


def test_creation_stores_optional_user_fields(client_session):
    client, _ = client_session
    fields = {"description": "CI automation", "first_name": "Build", "last_name": "Bot"}
    with (
        mock.patch.object(models, "User") as user,
        mock.patch.object(models, "Role"),
        mock.patch.object(models, "Permission"),
        mock.patch.object(models, "PermissionBinding"),
        mock.patch.object(models, "RoleBinding"),
        mock.patch.object(models, "ServiceAuthorizationCode"),
    ):
        models.IamClient.create_service_user_authorization(
            client,
            "automation",
            mock.Mock(),
            ["compute.node.read"],
            **fields,
            parent_user=mock.Mock(email="owner@example.com"),
        )
    for name, value in fields.items():
        assert user.call_args.kwargs[name] == value


@pytest.mark.parametrize(
    "fields",
    [
        {"description": "x" * 256},
        {"description": 123},
        {"first_name": "x" * 129},
        {"last_name": 123},
    ],
)
def test_invalid_optional_user_fields_create_no_records(client_session, fields):
    client, _ = client_session
    with (
        mock.patch.object(models, "Permission"),
        mock.patch.object(
            models.User,
            "_global_salt",
            new_callable=mock.PropertyMock,
            return_value="dGVzdA==",
        ),
        mock.patch.object(models.User, "insert") as insert,
        mock.patch.object(models, "Role") as role,
        mock.patch.object(models, "ServiceAuthorizationCode") as code,
    ):
        with pytest.raises(TypeError):
            models.IamClient.create_service_user_authorization(
                client,
                "automation",
                mock.Mock(),
                ["compute.node.read"],
                **fields,
                parent_user=mock.Mock(email="owner@example.com"),
            )
    insert.assert_not_called()
    role.assert_not_called()
    code.assert_not_called()


@pytest.mark.parametrize("domain_length", [83, 84, 95])
def test_creation_preserves_long_domains_with_valid_unique_emails(client_session, domain_length):
    client, session = client_session
    domain = "a" * 40 + "." + "b" * (domain_length - 45) + ".com"
    email_type = models.User.properties.properties["email"].get_property_type()
    assert email_type.validate("owner@" + domain)
    with (
        mock.patch.object(models.User, "_global_salt", new_callable=mock.PropertyMock, return_value="dGVzdA=="),
        mock.patch.object(models.User, "insert", autospec=True) as insert,
        mock.patch.object(models, "Role"),
        mock.patch.object(models, "Permission"),
        mock.patch.object(models, "PermissionBinding"),
        mock.patch.object(models, "RoleBinding"),
        mock.patch.object(models, "ServiceAuthorizationCode"),
    ):
        for name in ("automation-one", "automation-two"):
            models.IamClient.create_service_user_authorization(
                client, name, mock.Mock(), ["compute.node.read"],
                parent_user=mock.Mock(email="owner@" + domain),
            )
    users = [call.args[0] for call in insert.call_args_list]
    assert users[0].email != users[1].email
    for user in users:
        assert email_type.validate(user.email)
        assert user.email.rsplit("@", 1)[1] == domain
        local_part = user.email.split("@", 1)[0]
        assert local_part == (f"service-{user.uuid}" if domain_length == 83 else user.uuid.hex)
    assert all(call.kwargs["session"] is session for call in insert.call_args_list)


@pytest.mark.parametrize("domain_length", [96, 125])
def test_creation_rejects_oversized_domains_before_creating_records(client_session, domain_length):
    client, _ = client_session
    domain = "a" * 60 + "." + "b" * (domain_length - 65) + ".com"
    assert models.User.properties.properties["email"].get_property_type().validate("a@" + domain)
    with mock.patch.object(models, "User") as user:
        with pytest.raises(iam_exc.ServiceUserEmailDomainTooLong) as error:
            models.IamClient.create_service_user_authorization(
                client, "automation", mock.Mock(), ["compute.node.read"],
                parent_user=mock.Mock(email="a@" + domain),
            )
    assert error.value.code == 400
    user.assert_not_called()
    client._get_engine.assert_not_called()


def test_creation_reuses_a_service_user_left_by_a_revoked_code(client_session):
    client, session = client_session
    project = mock.Mock(uuid=uuid.uuid4())
    leftover = mock.Mock(uuid=uuid.uuid4())
    role = mock.Mock()
    client._find_reusable_service_user.return_value = (leftover, role)
    with (
        mock.patch.object(models, "User") as user,
        mock.patch.object(models, "Role") as new_role,
        mock.patch.object(models, "Permission"),
        mock.patch.object(models, "PermissionBinding") as permission_binding,
        mock.patch.object(models, "RoleBinding") as role_binding,
        mock.patch.object(models, "ServiceAuthorizationCode") as authorization,
    ):
        old_binding, old_code = mock.Mock(), mock.Mock()
        permission_binding.objects.get_all.return_value = [old_binding]
        authorization.objects.get_all.return_value = [old_code]
        result = models.IamClient.create_service_user_authorization(
            client,
            "automation",
            project,
            ["compute.node.read"],
            description="again",
            parent_user=mock.Mock(email="owner@example.com"),
        )
    client._find_reusable_service_user.assert_called_once_with(
        "automation", project, session
    )
    assert result["service_user_uuid"] == str(leftover.uuid)
    assert leftover.description == "again"
    leftover.update.assert_called_once_with(session=session)
    old_binding.delete.assert_called_once_with(session=session)
    old_code.delete.assert_called_once_with(session=session)
    assert authorization.objects.get_all.call_args.kwargs["locked"] is True
    assert permission_binding.call_args.kwargs["role"] is role
    permission_binding.return_value.insert.assert_called_once_with(session=session)
    assert authorization.call_args.kwargs["user"] is leftover
    assert (
        authorization.call_args.kwargs["code_hash"]
        == hashlib.sha256(result["code"].encode()).hexdigest()
    )
    authorization.return_value.insert.assert_called_once_with(session=session)
    user.assert_not_called()
    new_role.assert_not_called()
    role_binding.assert_not_called()


@pytest.fixture
def reusable_lookup():
    client = mock.Mock(uuid=uuid.uuid4())
    project = mock.Mock(uuid=uuid.uuid4())
    user = mock.Mock(uuid=uuid.uuid4(), type="service", registration_client=client.uuid)
    binding = mock.Mock(project=mock.Mock(uuid=project.uuid))
    binding.role.name = f"service-{user.uuid}"
    with (
        mock.patch.object(models, "User") as users,
        mock.patch.object(models, "Token") as tokens,
        mock.patch.object(models, "RoleBinding") as bindings,
    ):
        users.objects.query.return_value = [user]
        tokens.objects.get_all.return_value = []
        bindings.objects.get_all.return_value = [binding]
        yield SimpleNamespace(
            client=client,
            project=project,
            user=user,
            binding=binding,
            users=users,
            tokens=tokens,
            bindings=bindings,
        )


def _find(lookup):
    return models.IamClient._find_reusable_service_user(
        lookup.client, "Automation", lookup.project, "session"
    )


def test_reusable_lookup_returns_unused_service_user_and_role(reusable_lookup):
    assert _find(reusable_lookup) == (
        reusable_lookup.user,
        reusable_lookup.binding.role,
    )
    kwargs = reusable_lookup.users.objects.query.call_args.kwargs
    assert kwargs["where_values"] == ("automation",)
    assert kwargs["locked"] is True
    assert kwargs["session"] == "session"


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "human",
        "other_client",
        "has_token",
        "other_project",
        "extra_binding",
        "foreign_role",
    ],
)
def test_reusable_lookup_refuses_users_it_must_not_take_over(reusable_lookup, case):
    lookup = reusable_lookup
    if case == "missing":
        lookup.users.objects.query.return_value = []
    elif case == "human":
        lookup.user.type = "user"
    elif case == "other_client":
        lookup.user.registration_client = uuid.uuid4()
    elif case == "has_token":
        lookup.tokens.objects.get_all.return_value = [mock.Mock()]
    elif case == "other_project":
        lookup.binding.project.uuid = uuid.uuid4()
    elif case == "extra_binding":
        lookup.bindings.objects.get_all.return_value = [lookup.binding, mock.Mock()]
    elif case == "foreign_role":
        lookup.binding.role.name = "admin"
    assert _find(lookup) is None
