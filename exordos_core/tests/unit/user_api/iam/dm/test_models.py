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
import logging
from types import SimpleNamespace
from unittest import mock
import uuid

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


@pytest.mark.parametrize(
    "expired, wrong_client, wrong_redirect, warnings",
    [
        (False, False, False, []),
        (True, False, False, ["expired"]),
        (False, True, False, ["client mismatch"]),
        (False, False, True, ["redirect_uri mismatch"]),
        (True, True, True, ["expired", "client mismatch", "redirect_uri mismatch"]),
    ],
)
def test_authorization_code_logs_anomalies_but_returns_token(
    caplog, expired, wrong_client, wrong_redirect, warnings
):
    client = SimpleNamespace(uuid=uuid.uuid4())
    expected_client = SimpleNamespace(uuid=uuid.uuid4()) if wrong_client else client
    code = uuid.uuid4()
    token = object()
    auth_info = SimpleNamespace(
        uuid=uuid.uuid4(),
        idp=SimpleNamespace(iam_client=expected_client),
        redirect_uri="https://app.example/callback",
        expiration_time_at=datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(hours=-1 if expired else 1),
        token=token,
        delete=mock.Mock(),
    )
    redirect_uri = (
        "https://other.example/callback" if wrong_redirect else auth_info.redirect_uri
    )
    with (
        mock.patch.object(
            type(models.IdpAuthorizationInfo.objects),
            "get_all",
            return_value=[auth_info],
        ),
        caplog.at_level(logging.WARNING, logger=models.__name__),
    ):
        result = models.IamClient.get_token_by_authorization_code(
            client, code, redirect_uri
        )

    assert result is token
    auth_info.delete.assert_called_once()
    assert len(caplog.records) == len(warnings)
    for warning in warnings:
        assert any(warning in record.message for record in caplog.records)
    assert str(code) not in caplog.text
