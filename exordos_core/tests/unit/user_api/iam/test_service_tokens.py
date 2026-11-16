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
import typing as tp
from unittest import mock
import uuid

import pytest
from restalchemy.dm import filters

from exordos_core.user_api.iam import exceptions
from exordos_core.user_api.iam.dm import models


def _matches(clause: tp.Any, value: tp.Any) -> bool:
    """Apply collection filters to fixture rows, retaining project boundaries."""
    if isinstance(clause, dict):
        return all(
            _matches(part, getattr(value, name)) for name, part in clause.items()
        )
    if isinstance(clause, filters.AND):
        return all(_matches(part, value) for part in clause.clauses)
    if isinstance(clause, filters.OR):
        return any(_matches(part, value) for part in clause.clauses)
    value = getattr(value, "uuid", value)
    if isinstance(clause, filters.In):
        return value in clause.value
    assert isinstance(clause, (filters.EQ, filters.Is))
    return value == clause.value


@pytest.mark.parametrize(
    "binding_scope,permission,allowed",
    [
        ("target", "iam.service_token.create", True),
        ("global", "*.*.*", True),
        ("global", "iam.*.*", True),
        ("global", "iam.service_token.*", True),
        ("global", "iam.service_token.create", True),
        ("target", "*.*.*", True),
        ("other", "*.*.*", False),
        ("other", "iam.service_token.create", False),
        ("global", "compute.node.read", False),
        ("target", "iam.user.read", False),
        ("none", "*.*.*", False),
    ],
)
@pytest.mark.parametrize("service_has_project", [True, False])
def test_service_token_respects_global_and_project_permissions(
    binding_scope: str,
    permission: str,
    allowed: bool,
    service_has_project: bool,
) -> None:
    target, other, role, perm_id = (uuid.uuid4() for _ in range(4))
    caller = SimpleNamespace(uuid=uuid.uuid4())
    service = SimpleNamespace(uuid=uuid.uuid4(), type="service")
    scopes = {"global": None, "target": target, "other": other, "none": None}
    bindings = (
        []
        if binding_scope == "none"
        else [
            SimpleNamespace(user=caller.uuid, project=scopes[binding_scope], role=role),
        ]
    )
    if service_has_project:
        bindings.append(SimpleNamespace(user=service.uuid, project=target, role=role))
    permissions = [
        SimpleNamespace(
            role=role,
            permission=SimpleNamespace(
                uuid=perm_id
                if permission == "iam.service_token.create"
                else uuid.uuid4(),
                name=permission,
            ),
        ),
    ]
    client = mock.Mock(spec=models.IamClient)
    client._extract_project_from_scope.side_effect = lambda scope: (
        models.IamClient._extract_project_from_scope(client, scope)
    )
    client._check_service_token_permission.side_effect = lambda rows: (
        models.IamClient._check_service_token_permission(client, rows)
    )
    with (
        mock.patch.object(models.RoleBinding, "objects") as roles,
        mock.patch.object(models.PermissionBinding, "objects") as grants,
        mock.patch.object(models.Permission, "objects") as permission_rows,
        mock.patch.object(models.User, "objects") as users,
    ):
        roles.get_all.side_effect = lambda **kw: [
            row for row in bindings if _matches(kw["filters"], row)
        ]
        grants.get_all.side_effect = lambda **kw: [
            row for row in permissions if _matches(kw["filters"], row)
        ]
        permission_rows.get_one.return_value = SimpleNamespace(uuid=perm_id)
        users.get_one.return_value = service

        def request() -> tp.Any:
            return models.IamClient._handle_service_account_token_request(
                client,
                caller,
                service.uuid,
                f"project:{target} profile",
                ttl=3600,
            )

        if allowed and service_has_project:
            result = request()
            assert result is client._create_token_for_user.return_value
            client._create_token_for_user.assert_called_once_with(
                user=service,
                scope=f"project:{target}",
                ttl=3600,
            )
            assert users.get_one.call_args.kwargs["filters"]["type"] == "service"
        else:
            with pytest.raises(exceptions.CanNotCreateServiceToken):
                request()
            client._create_token_for_user.assert_not_called()
