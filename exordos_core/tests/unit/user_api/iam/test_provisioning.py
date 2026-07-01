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

from exordos_core.user_api.iam import constants as iam_c
from exordos_core.user_api.iam.dm import models


class TestProvisionPersonalWorkspace:
    @pytest.fixture
    def user(self):
        u = models.User.__new__(models.User)
        u.name = "testuser"
        u.uuid = uuid.uuid4()
        return u

    @pytest.fixture
    def org(self):
        o = mock.MagicMock()
        o.uuid = uuid.uuid4()
        return o

    @pytest.fixture
    def project(self):
        p = mock.MagicMock()
        p.uuid = uuid.uuid4()
        return p

    def test_creates_org_with_username_as_name(self, user, org, project):
        with (
            mock.patch.object(models, "Organization") as MockOrg,
            mock.patch.object(models, "OrganizationMember"),
            mock.patch.object(models, "Project") as MockProject,
        ):
            MockOrg.return_value = org
            MockProject.return_value = project

            user.provision_personal_workspace()

            MockOrg.assert_called_once_with(
                name=user.name,
                description="Personal workspace",
            )
            org.insert.assert_called_once()

    def test_assigns_user_as_org_owner(self, user, org, project):
        with (
            mock.patch.object(models, "Organization") as MockOrg,
            mock.patch.object(models, "OrganizationMember") as MockMember,
            mock.patch.object(models, "Project") as MockProject,
        ):
            MockOrg.return_value = org
            MockProject.return_value = project

            user.provision_personal_workspace()

            MockMember.assert_called_once_with(
                organization=org,
                user=user,
                role=iam_c.OrganizationRole.OWNER.value,
            )
            MockMember.return_value.insert.assert_called_once()

    def test_creates_default_project_in_org(self, user, org, project):
        with (
            mock.patch.object(models, "Organization") as MockOrg,
            mock.patch.object(models, "OrganizationMember"),
            mock.patch.object(models, "Project") as MockProject,
        ):
            MockOrg.return_value = org
            MockProject.return_value = project

            user.provision_personal_workspace()

            MockProject.assert_called_once_with(
                name="default",
                description="Default project",
                organization=org,
            )
            project.insert.assert_called_once()

    def test_calls_add_owner_with_self(self, user, org, project):
        with (
            mock.patch.object(models, "Organization") as MockOrg,
            mock.patch.object(models, "OrganizationMember"),
            mock.patch.object(models, "Project") as MockProject,
        ):
            MockOrg.return_value = org
            MockProject.return_value = project

            user.provision_personal_workspace()

            project.add_owner.assert_called_once_with(user)
