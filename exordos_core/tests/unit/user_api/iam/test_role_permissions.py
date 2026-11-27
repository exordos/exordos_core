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
from restalchemy.dm import filters as ra_filters

from exordos_core.user_api.iam.api import controllers
from exordos_core.user_api.iam.dm import models


def matches(row, expression):
    """Evaluate the ORM filter against independent role-binding fixtures."""
    if isinstance(expression, ra_filters.OR):
        return any(matches(row, clause) for clause in expression.clauses)
    if isinstance(expression, ra_filters.AND):
        return all(matches(row, clause) for clause in expression.clauses)
    return all(
        getattr(row, field) == clause.value for field, clause in expression.items()
    )


@pytest.fixture
def role_permissions():
    user = SimpleNamespace(uuid=uuid.uuid4())
    other_user = SimpleNamespace(uuid=uuid.uuid4())
    project = SimpleNamespace(uuid=uuid.uuid4())
    other_project = SimpleNamespace(uuid=uuid.uuid4())
    rows = []
    for owner, scope, name in (
        (user, None, "iam.user.delete"),
        (user, project, "compute.node.read"),
        (user, other_project, "compute.node.delete"),
        (other_user, None, "*.*.*"),
        (other_user, project, "iam.user.delete_all"),
    ):
        permission = SimpleNamespace(uuid=uuid.uuid4(), name=name)
        rows.append(SimpleNamespace(user=owner, project=scope, permission=permission))

    def get_all(*, filters, limit=None):
        if isinstance(filters, dict) and "name" in filters:
            selected = [
                row.permission for row in rows if matches(row.permission, filters)
            ]
        else:
            selected = [row for row in rows if matches(row, filters)]
        return selected[:limit] if limit else selected

    with (
        mock.patch.object(
            type(models.PermissionFastView.objects), "get_all", side_effect=get_all
        ),
        mock.patch.object(models.User, "me", return_value=user),
        mock.patch.object(models, "Introspection", side_effect=SimpleNamespace),
    ):
        yield user, project


@pytest.mark.parametrize("scoped", [False, True])
def test_introspection_combines_only_global_and_current_project_roles(
    role_permissions, scoped
):
    user, project = role_permissions
    token = SimpleNamespace(user=user, project=project if scoped else None)
    result = models.Token.introspect(token)
    expected = {"iam.user.delete"}
    if scoped:
        expected.add("compute.node.read")
    assert {permission.name for permission in result.permissions} == expected
    assert result.project is token.project


@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize(
    "name",
    [
        "iam.user.delete",
        "compute.node.read",
        "compute.node.delete",
        "*.*.*",
        "iam.user.delete_all",
        "missing.permission",
    ],
)
def test_permission_checks_respect_user_and_project_boundaries(
    role_permissions, scoped, name
):
    user, project = role_permissions
    token = SimpleNamespace(user=user, project=project if scoped else None)
    expected = name == "iam.user.delete" or (scoped and name == "compute.node.read")
    assert models.Token.has_permission(token, name) is expected


@pytest.mark.parametrize("include_global", [False, True])
def test_token_issuance_checks_global_permissions_too(role_permissions, include_global):
    user, project = role_permissions
    controller = object.__new__(controllers.TokenController)
    controller._ctx_project_id = project.uuid
    permissions = ["compute.node.read"]
    if include_global:
        permissions.append("iam.user.delete")
    controller._enforcer = enforcers.Enforcer(permissions)
    controller.enforce = controller._enforcer.enforce
    if include_global:
        controller._enforce_token_permissions(user, project)
    else:
        with pytest.raises(iam_exc.PolicyNotAuthorized):
            controller._enforce_token_permissions(user, project)
