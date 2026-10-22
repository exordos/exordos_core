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
