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

import datetime
from types import SimpleNamespace
from unittest import mock

import pytest

from exordos_core.user_api.iam.dm import models


def test_project_list_my_returns_each_project_once():
    first_project = SimpleNamespace(uuid="project-1")
    duplicate_project = SimpleNamespace(uuid="project-1")
    second_project = SimpleNamespace(uuid="project-2")
    role_bindings = [
        SimpleNamespace(project=first_project),
        SimpleNamespace(project=duplicate_project),
        SimpleNamespace(project=second_project),
    ]

    with (
        mock.patch.object(models.User, "me"),
        mock.patch.object(
            type(models.RoleBinding.objects),
            "get_all",
            return_value=role_bindings,
        ),
    ):
        projects = models.Project.list_my()

    assert projects == [first_project, second_project]


@pytest.mark.parametrize("model", [models.ManagedToken, models.Token])
def test_renewal_issuance_time(model, monkeypatch):
    monkeypatch.setattr(models.ModelWithSecret, "_global_salt", "test-salt")
    created_at = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    lifetime = datetime.timedelta(hours=1)
    token = model(
        user=models.User(
            name="token_user", email="token@example.com", secret="test-password"
        ),
        iam_client=models.IamClient(
            name="client", client_id="client", secret="test-secret"
        ),
        issuer="https://issuer.example",
        created_at=created_at,
        expiration_at=created_at + lifetime,
        expiration_delta=lifetime,
    )
    assert token._get_access_token_info()["iat"] == int(created_at.timestamp())

    with mock.patch.object(model, "update"):
        for elapsed in (datetime.timedelta(minutes=40), datetime.timedelta(days=30)):
            issued_at = created_at + elapsed
            token.renew(issued_at)
            claims = token._get_access_token_info()
            assert claims["auth_time"] == int(created_at.timestamp())
            if model is models.ManagedToken:
                assert claims["iat"] == int(issued_at.timestamp())
                assert claims["exp"] - claims["iat"] == int(lifetime.total_seconds())
            else:
                assert claims["iat"] == int(created_at.timestamp())


@pytest.fixture
def managed_token(monkeypatch):
    monkeypatch.setattr(models.ModelWithSecret, "_global_salt", "test-salt")
    return models.ManagedToken(
        user=models.User(
            name="token_user", email="token@example.com", secret="test-password"
        ),
        iam_client=models.IamClient(
            name="client", client_id="client", secret="test-secret"
        ),
        issuer="https://issuer.example",
    )


def test_managed_token_creation_retries_missing_project(managed_token):
    from restalchemy.storage import exceptions

    project = models.Project(
        name="token-project", organization=models.Organization(name="token-org")
    )
    lookup = type(models.Project.objects)
    with (
        mock.patch.object(lookup, "get_one") as get_project,
        mock.patch.object(lookup, "get_all", return_value=[mock.sentinel.binding]),
    ):
        get_project.side_effect = exceptions.RecordNotFound(
            model=models.Project, filters={}
        )
        kwargs = {
            "user": managed_token.user,
            "iam_client": managed_token.iam_client,
            "issuer": managed_token.issuer,
            "scope": f"project:{project.uuid}",
        }
        with pytest.raises(exceptions.RecordNotFound):
            models.ManagedToken(**kwargs)
        get_project.side_effect = None
        get_project.return_value = project
        token = models.ManagedToken(**kwargs)
    assert token.project is project


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("has_binding", [False, True])
def test_managed_token_requires_project_membership(
    managed_token, operation, has_binding
):
    own_project = models.Project(
        name="own-project", organization=models.Organization(name="own-org")
    )
    foreign_project = models.Project(
        name="foreign-project", organization=models.Organization(name="foreign-org")
    )
    managed_token.project = own_project
    scope = f"project:{foreign_project.uuid}"
    with (
        mock.patch.object(
            type(models.Project.objects), "get_one", return_value=foreign_project
        ),
        mock.patch.object(
            type(models.RoleBinding.objects),
            "get_all",
            return_value=[mock.sentinel.binding] if has_binding else [],
        ) as get_bindings,
        mock.patch.object(models.Token, "update") as update,
    ):

        def apply_scope():
            if operation == "create":
                return models.ManagedToken(
                    user=managed_token.user,
                    iam_client=managed_token.iam_client,
                    issuer=managed_token.issuer,
                    scope=scope,
                )
            managed_token.scope = scope
            managed_token.update()
            return managed_token

        if has_binding:
            assert apply_scope().project is foreign_project
        else:
            with pytest.raises(models.iam_e.Forbidden):
                apply_scope()
            assert managed_token.project is own_project
            update.assert_not_called()

    filters = get_bindings.call_args.kwargs["filters"]
    assert filters["user"].value is managed_token.user
    assert filters["project"].value is foreign_project


def test_managed_token_update_does_not_repair_missing_project(managed_token):
    managed_token.scope = "project:12345678-c625-4fee-81d5-f691897b8142"
    with (
        mock.patch.object(type(models.Project.objects), "get_one") as get_project,
        mock.patch.object(models.Token, "update") as update,
        mock.patch.object(
            type(managed_token.properties["scope"]), "is_dirty", return_value=False
        ),
    ):
        managed_token.update()
    assert managed_token.project is None
    get_project.assert_not_called()
    update.assert_called_once()
