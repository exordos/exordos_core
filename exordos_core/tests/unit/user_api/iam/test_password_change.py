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

import pytest
import webob

from exordos_core.common.api.middlewares import errors
from exordos_core.user_api.iam import exceptions
from exordos_core.user_api.iam.api import controllers
from exordos_core.user_api.iam.dm import models


@pytest.mark.parametrize("kind", ["KEYCLOAK", "OTHER"])
@pytest.mark.parametrize("is_me", [True, False])
def test_external_password_change_forbidden(kind, is_me):
    resource = mock.Mock(user_source=SimpleNamespace(KIND=kind))
    controller = object.__new__(controllers.UserController)
    with (
        mock.patch.object(controller, "validate"),
        mock.patch.object(controller, "enforce", return_value=True),
        mock.patch.object(models.User, "me", return_value=resource if is_me else None),
        pytest.raises(exceptions.ExternalPasswordChangeForbidden) as caught,
    ):
        controller.change_password._post(controller, resource, "old", "new")
    resource.change_secret_safe.assert_not_called()
    response = errors.ErrorsHandlerMiddleware(None)._construct_error_response(
        webob.Request.blank("/"), caught.value
    )
    assert response.status_int == 403
    assert "Password changes are forbidden" in response.text


@pytest.mark.parametrize("is_me", [True, False])
def test_iam_password_change_unchanged(is_me):
    resource = mock.Mock(user_source=models.IamUserSource())
    controller = object.__new__(controllers.UserController)
    with (
        mock.patch.object(controller, "validate") as validate,
        mock.patch.object(controller, "enforce", return_value=True),
        mock.patch.object(models.User, "me", return_value=resource if is_me else None),
    ):
        assert (
            controller.change_password._post(controller, resource, "old", "new")
            is resource
        )
    validate.assert_called_once_with("new")
    resource.change_secret_safe.assert_called_once_with(
        old_secret="old", new_secret="new"
    )
