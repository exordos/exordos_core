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
from gcl_sdk.infra import constants as infra_c
from restalchemy.api import actions
from restalchemy.api import constants as ra_c
from restalchemy.api import controllers
from restalchemy.api import field_permissions as field_p
from restalchemy.api import resources
from restalchemy.common import exceptions as ra_e
from restalchemy.dm import filters as dm_filters

from exordos_core.vs.dm import models as models


class ValueNotBelongsToVariableError(ra_e.ValidationErrorException):
    message = "Value does not belong to the variable"


class NoValueSelectedError(ra_e.ValidationErrorException):
    message = "No value selected"


class ValuesStoreController(controllers.RoutesListController):
    __TARGET_PATH__ = "/v1/vs/"


class ProfilesController(
    iam_controllers.PolicyBasedWithoutProjectController,
    controllers.BaseResourceControllerPaginated,
):
    """Controller for /v1/vs/profiles/ endpoint"""

    __policy_name__ = "profile"
    __policy_service_name__ = "vs"

    __resource__ = resources.ResourceByRAModel(
        model_class=models.Profile,
        process_filters=True,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "status": {ra_c.ALL: field_p.Permissions.RO},
            },
        ),
    )

    @actions.post
    def activate(self, resource: models.Profile):
        self._enforce("activate")
        resource.activate()
        return resource


class VariablesController(
    iam_controllers.PolicyBasedWithoutProjectController,
    controllers.BaseResourceControllerPaginated,
):
    """Controller for /v1/vs/variables/ endpoint"""

    __policy_name__ = "variable"
    __policy_service_name__ = "vs"

    __resource__ = resources.ResourceByRAModel(
        model_class=models.Variable,
        process_filters=True,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "status": {ra_c.ALL: field_p.Permissions.RO},
                "value": {ra_c.ALL: field_p.Permissions.RO},
                "manual_selected": {ra_c.ALL: field_p.Permissions.RO},
            },
        ),
    )

    def update(self, uuid, **kwargs):
        kwargs["status"] = infra_c.VariableStatus.IN_PROGRESS.value
        return super().update(uuid, **kwargs)

    @actions.post
    def select_value(self, resource: models.Variable, value: str):
        self._enforce("select_value")
        value = models.Value.objects.get_one(
            filters={"uuid": dm_filters.EQ(value)},
        )
        try:
            value.select_me(resource)
        except ValueError:
            raise ValueNotBelongsToVariableError()

        # Need to force update to rebuild the variable
        resource.update(force=True)
        return resource

    @actions.post
    def release_value(self, resource: models.Variable):
        self._enforce("release_value")
        if resource.selected_value is None:
            raise NoValueSelectedError()
        resource.release_value()

        # Need to force update to rebuild the variable
        resource.update(force=True)
        return resource


class ValuesController(
    iam_controllers.PolicyBasedController,
    controllers.BaseResourceControllerPaginated,
):
    """Controller for /v1/vs/values/ endpoint"""

    __policy_name__ = "value"
    __policy_service_name__ = "vs"

    __resource__ = resources.ResourceByRAModel(
        model_class=models.Value,
        process_filters=True,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "status": {ra_c.ALL: field_p.Permissions.RO},
            },
        ),
    )
