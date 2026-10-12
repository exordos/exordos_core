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
import uuid

from gcl_iam import enforcers
from gcl_iam import exceptions as iam_e
import pytest

from exordos_core.user_api.iam import exceptions as core_iam_e
from exordos_core.user_api.iam.api import controllers
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
    "old_project, new_project, member, wildcard, allowed",
    [
        ("a", "a", False, False, True),
        ("a", "b", True, False, False),
        ("a", None, True, False, False),
        ("a", "b", True, True, False),
        (None, "a", True, False, True),
        (None, "a", False, False, False),
        (None, "a", False, True, True),
        (None, None, False, False, True),
    ],
)
def test_refresh_enforces_project_boundary(
    old_project, new_project, member, wildcard, allowed
):
    old = SimpleNamespace(uuid=old_project) if old_project else None
    new = SimpleNamespace(uuid=new_project) if new_project else None
    expiration = datetime.datetime.now(datetime.timezone.utc)
    token = SimpleNamespace(
        user=SimpleNamespace(uuid=uuid.uuid4()),
        project=old,
        scope="openid",
        expiration_at=expiration,
        expiration_delta=datetime.timedelta(hours=1),
        _get_project_by_scope=mock.Mock(return_value=new),
        has_permission=mock.Mock(return_value=wildcard),
        update=mock.Mock(),
    )
    scope = f"project:{new_project}" if new_project else "profile"
    with mock.patch.object(
        type(models.RoleBinding.objects),
        "get_all",
        return_value=[object()] if member else [],
    ):
        if allowed:
            models.Token.refresh(token, scope)
            assert token.project is new
            assert token.scope == scope
            token.update.assert_called_once()
        else:
            with pytest.raises(iam_e.Forbidden):
                models.Token.refresh(token, scope)
            assert token.project is old
            assert token.scope == "openid"
            assert token.expiration_at == expiration
            token.update.assert_not_called()


def test_refresh_rejects_unknown_project_before_mutation():
    token = SimpleNamespace(
        user=object(),
        project=None,
        _get_project_by_scope=mock.Mock(return_value=None),
        update=mock.Mock(),
    )
    with pytest.raises(iam_e.Forbidden):
        models.Token.refresh(token, f"project:{uuid.uuid4()}")
    token.update.assert_not_called()


@pytest.mark.parametrize(
    "permission, caller_scope, allowed",
    [
        (False, "same", False),
        (True, "other", False),
        (True, "same", True),
        (True, None, True),
    ],
)
def test_service_exchange_checks_active_caller(permission, caller_scope, allowed):
    project = uuid.uuid4()
    caller_project = (
        project if caller_scope == "same" else uuid.uuid4() if caller_scope else None
    )
    ctx = mock.Mock()
    ctx.get_real_url_with_prefix.return_value = "https://core.example/"
    ctx.iam_context.enforcer = enforcers.Enforcer(
        ["iam.service_token.create"] if permission else []
    )
    ctx.iam_context.introspection_info.return_value = {"project_id": caller_project}
    controller = SimpleNamespace(get_context=lambda: ctx)
    resource = mock.Mock()
    resource._extract_project_from_scope = lambda scope: (
        models.IamClient._extract_project_from_scope(resource, scope)
    )
    with mock.patch.object(models.User, "me"):
        if allowed:
            controllers.ClientsController.get_token._post(
                controller,
                resource,
                "access_token",
                service_account_uuid=uuid.uuid4(),
                scope=f"project:{project}",
            )
            resource._handle_service_account_token_request.assert_called_once()
        else:
            with pytest.raises(
                (iam_e.PolicyNotAuthorized, core_iam_e.CanNotCreateServiceToken)
            ):
                controllers.ClientsController.get_token._post(
                    controller,
                    resource,
                    "access_token",
                    service_account_uuid=uuid.uuid4(),
                    scope=f"project:{project}",
                )
            resource._handle_service_account_token_request.assert_not_called()
