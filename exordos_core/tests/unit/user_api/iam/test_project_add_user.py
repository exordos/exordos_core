#    Copyright 2025-2026 Genesis Corporation.
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

from unittest import mock
import uuid

import pytest

from exordos_core.common import constants as common_c
from exordos_core.user_api.iam import constants as c
from exordos_core.user_api.iam.api import controllers
from exordos_core.user_api.iam.dm import models


@pytest.mark.parametrize(
    "user", ["invitee", "Invitee@example.com", "79cb0699-037b-4f62-9fc9-bcd739c04466"]
)
@pytest.mark.parametrize("membership_role", [None, "MEMBER", "OWNER"])
@pytest.mark.parametrize("existing_binding", [False, True])
def test_project_invitation_ensures_organization_membership(
    membership_role, existing_binding, user
):
    controller = mock.Mock()
    project = mock.Mock(uuid=uuid.uuid4())
    target_user = mock.Mock(uuid=uuid.uuid4())
    role = mock.Mock(uuid=common_c.OWNER_ROLE_UUID)
    controller._resolve_user.side_effect = controllers.ProjectController._resolve_user
    controller._resolve_role.return_value = role
    membership = (
        None if membership_role is None else mock.Mock(role=membership_role)
    )
    binding = mock.Mock() if existing_binding else None
    with (
        mock.patch.object(models, "Role") as roles,
        mock.patch.object(models, "RoleBinding") as bindings,
        mock.patch.object(models, "User") as users,
        mock.patch.object(models, "OrganizationMember") as members,
    ):
        roles.objects.get_one.return_value = role
        bindings.objects.get_one_or_none.side_effect = [mock.Mock(), binding]
        users.objects.get_one.return_value = target_user
        members.objects.get_one_or_none.return_value = membership

        result = controllers.ProjectController.add_user._post(
            controller, project, user, "owner"
        )

        users.objects.get_one.assert_called_once()
        if membership is None:
            members.assert_called_once_with(
                organization=project.organization,
                user=target_user,
                role=c.OrganizationRole.MEMBER.value,
            )
            members.return_value.insert.assert_called_once_with()
        else:
            members.assert_not_called()
            assert membership.role == membership_role
            membership.save.assert_not_called()
        if existing_binding:
            bindings.assert_not_called()
            assert result == binding.get_storable_snapshot.return_value
        else:
            bindings.assert_called_once_with(
                project=project, user=target_user, role=role
            )
            bindings.return_value.save.assert_called_once_with()


def test_rejected_project_invitation_does_not_create_membership():
    controller = mock.Mock()
    controller.enforce.return_value = False
    with (
        mock.patch.object(models, "Role"),
        mock.patch.object(models, "RoleBinding") as bindings,
        mock.patch.object(models, "User"),
        mock.patch.object(models, "OrganizationMember") as members,
    ):
        bindings.objects.get_one_or_none.return_value = None
        with pytest.raises(controllers.iam_e.CanNotAddUserToProject):
            controllers.ProjectController.add_user._post(
                controller, mock.Mock(), "invitee", "owner"
            )
        members.objects.get_one_or_none.assert_not_called()
        members.assert_not_called()
        controller._resolve_user.assert_not_called()
