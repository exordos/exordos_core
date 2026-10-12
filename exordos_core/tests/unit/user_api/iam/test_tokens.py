#    Copyright 2026 Genesis Corporation.
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

from types import SimpleNamespace
from unittest import mock
import uuid

from gcl_iam import enforcers
from gcl_iam import exceptions as iam_exc
import pytest
from restalchemy.api import controllers as ra_controllers
from restalchemy.common import exceptions as ra_exc

from exordos_core.user_api.iam.api import controllers
from exordos_core.user_api.iam.dm import models


@pytest.fixture
def setup_token_controller():
    user = SimpleNamespace(uuid=uuid.uuid4(), name="creator")
    project = SimpleNamespace(uuid=uuid.uuid4())
    token = mock.Mock(user=user, project=project, uuid=uuid.uuid4())
    controller = object.__new__(controllers.TokenController)
    controller._enforcer = enforcers.Enforcer(["iam.token.create", "iam.token.update"])
    controller._ctx_project_id = project.uuid
    controller.enforce = controller._enforcer.enforce
    controller._req = mock.Mock()
    model = mock.Mock(return_value=token)
    with (
        mock.patch.object(models.User, "me", return_value=user),
        mock.patch.object(
            controllers.TokenController,
            "model",
            new_callable=mock.PropertyMock,
            return_value=model,
        ),
        mock.patch.object(
            type(models.PermissionFastView.objects), "get_all", return_value=[]
        ) as permissions,
        mock.patch.object(
            ra_controllers.BaseResourceController, "get", return_value=token
        ),
    ):
        yield controller, token, permissions


def test_create_rejects_permissions_missing_from_creator(setup_token_controller):
    controller, token, permissions = setup_token_controller
    permissions.return_value = [
        SimpleNamespace(permission=SimpleNamespace(name="compute.node.delete"))
    ]
    with pytest.raises(iam_exc.PolicyNotAuthorized):
        controller.create(scope=f"project:{token.project.uuid}")
    token.insert.assert_not_called()


def test_create_allows_own_permissions(setup_token_controller):
    controller, token, permissions = setup_token_controller
    permissions.return_value = [
        SimpleNamespace(permission=SimpleNamespace(name="iam.token.create"))
    ]
    assert controller.create() is token
    token.insert.assert_called_once()
    filters = permissions.call_args.kwargs["filters"]
    assert filters["user"].value is token.user
    assert filters["project"].value is token.project


@pytest.mark.parametrize("project", [None, SimpleNamespace(uuid=uuid.uuid4())])
def test_create_cannot_leave_callers_project(setup_token_controller, project):
    controller, token, _ = setup_token_controller
    token.project = project
    with pytest.raises(iam_exc.Forbidden):
        controller.create()
    token.insert.assert_not_called()


def test_create_requires_create_permission(setup_token_controller):
    controller, token, _ = setup_token_controller
    controller._enforcer = enforcers.Enforcer([])
    with pytest.raises(iam_exc.PolicyNotAuthorized):
        controller.create()
    token.insert.assert_not_called()


def test_create_all_keeps_explicit_delegation(setup_token_controller):
    controller, token, permissions = setup_token_controller
    controller._enforcer = enforcers.Enforcer(
        ["iam.token.create", "iam.token.create_all"]
    )
    controller.enforce = controller._enforcer.enforce
    token.project = None
    token.user = SimpleNamespace(uuid=uuid.uuid4(), name="other")
    assert controller.create(user=token.user) is token
    permissions.assert_not_called()
    token.insert.assert_called_once()


def test_update_rejects_user_even_with_create_all(setup_token_controller):
    controller, token, _ = setup_token_controller
    controller._enforcer = enforcers.Enforcer(["*.*.*"])
    controller.enforce = controller._enforcer.enforce
    with pytest.raises(ra_exc.FieldPermissionError):
        controller.update(token.uuid, user=SimpleNamespace(uuid=uuid.uuid4()))
    token.update_dm.assert_not_called()
    token.update.assert_not_called()


def test_update_scope_cannot_escalate_permissions(setup_token_controller):
    controller, token, permissions = setup_token_controller
    controller._ctx_project_id = None
    token._get_project_by_scope.return_value = token.project
    permissions.return_value = [
        SimpleNamespace(permission=SimpleNamespace(name="compute.node.delete"))
    ]
    with pytest.raises(iam_exc.PolicyNotAuthorized):
        controller.update(token.uuid, scope=f"project:{token.project.uuid}")
    token.update_dm.assert_not_called()
    token.update.assert_not_called()


def test_update_scope_allows_own_permissions(setup_token_controller):
    controller, token, permissions = setup_token_controller
    token._get_project_by_scope.return_value = token.project
    permissions.return_value = [
        SimpleNamespace(permission=SimpleNamespace(name="iam.token.create"))
    ]
    scope = f"project:{token.project.uuid}"
    assert controller.update(token.uuid, scope=scope) is token
    token.update_dm.assert_called_once_with(values={"scope": scope})
    token.update.assert_called_once()


@pytest.mark.parametrize("reason", ["permissions", "project", "user"])
def test_regenerate_cannot_exceed_callers_rights(setup_token_controller, reason):
    controller, token, permissions = setup_token_controller
    if reason == "permissions":
        permissions.return_value = [
            SimpleNamespace(permission=SimpleNamespace(name="compute.node.delete"))
        ]
    elif reason == "project":
        token.project = SimpleNamespace(uuid=uuid.uuid4())
    else:
        token.user = SimpleNamespace(uuid=uuid.uuid4(), name="other")
    with pytest.raises(iam_exc.CommonForbiddenError):
        controllers.TokenController.regenerate._post(controller, token)
    token.regenerate.assert_not_called()


def test_regenerate_allows_own_permissions(setup_token_controller):
    controller, token, permissions = setup_token_controller
    permissions.return_value = [
        SimpleNamespace(permission=SimpleNamespace(name="iam.token.create"))
    ]
    token.access_token = "replacement"
    assert controllers.TokenController.regenerate._post(controller, token) == {
        "access_token": "replacement"
    }
    token.regenerate.assert_called_once()


def test_regenerate_create_all_keeps_delegation(setup_token_controller):
    controller, token, permissions = setup_token_controller
    controller._enforcer = enforcers.Enforcer(
        ["iam.token.update", "iam.token.create_all"]
    )
    controller.enforce = controller._enforcer.enforce
    token.project = None
    token.user = SimpleNamespace(uuid=uuid.uuid4(), name="other")
    token.access_token = "replacement"
    assert controllers.TokenController.regenerate._post(controller, token) == {
        "access_token": "replacement"
    }
    token.regenerate.assert_called_once()
    permissions.assert_not_called()
