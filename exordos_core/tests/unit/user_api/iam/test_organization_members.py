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

from exordos_core.user_api.iam.api import controllers
from exordos_core.user_api.iam.dm import models


@pytest.mark.parametrize("role", ["OWNER", "MEMBER"])
@pytest.mark.parametrize("is_self", [False, True])
@pytest.mark.parametrize("is_owner", [False, True])
def test_delete_membership_preserves_own_ownership(role, is_self, is_owner):
    controller = controllers.OrganizationMemberController(mock.Mock())
    organization = mock.Mock()
    organization.are_i_owner.return_value = is_owner
    actor = mock.Mock(uuid=uuid.uuid4())
    member = mock.Mock(role=role)
    member.user = actor if is_self else mock.Mock(uuid=uuid.uuid4())
    member_uuid = uuid.uuid4()
    with (
        mock.patch.object(controller, "enforce", return_value=True),
        mock.patch.object(controller, "get", return_value=member) as get,
        mock.patch.object(models.User, "me", return_value=actor),
        mock.patch.object(
            controllers.controllers.BaseNestedResourceControllerPaginated, "delete"
        ) as delete,
    ):
        if is_self and role == "OWNER":
            with pytest.raises(controllers.iam_e.CanNotUpdateOrganization):
                controller.delete(organization, member_uuid)
            delete.assert_not_called()
        else:
            controller.delete(organization, member_uuid)
            delete.assert_called_once_with(organization, member_uuid)
        get.assert_called_once_with(parent_resource=organization, uuid=member_uuid)
