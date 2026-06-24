#    Copyright 2025 Genesis Corporation.
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

from gcl_iam.api import controllers as iam_controllers
from restalchemy.api import actions
from restalchemy.api import constants as ra_c
from restalchemy.api import controllers
from restalchemy.api import field_permissions as field_p
from restalchemy.api import resources

from exordos_core.repo.dm import models


class RepoProxyController(controllers.RoutesListController):
    __TARGET_PATH__ = "/v1/repo/"


class RepositoryController(
    iam_controllers.PolicyBasedController,
    controllers.BaseResourceControllerPaginated,
):
    __policy_service_name__ = "repo"
    __policy_name__ = "repository"

    __resource__ = resources.ResourceByRAModel(
        models.Repository,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "status": {ra_c.ALL: field_p.Permissions.RO},
                "created_at": {ra_c.ALL: field_p.Permissions.RO},
                "updated_at": {ra_c.ALL: field_p.Permissions.RO},
            },
        ),
    )

    @actions.post
    def refresh(self, resource: models.Repository):
        return resource


class RepoElementController(
    iam_controllers.PolicyBasedController,
    controllers.BaseResourceControllerPaginated,
):
    __policy_service_name__ = "repo"
    __policy_name__ = "element"

    __resource__ = resources.ResourceByRAModel(
        models.RepoElement,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "status": {ra_c.ALL: field_p.Permissions.RO},
                "created_at": {ra_c.ALL: field_p.Permissions.RO},
                "updated_at": {ra_c.ALL: field_p.Permissions.RO},
            },
        ),
    )
