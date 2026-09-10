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

from exordos_core.elements.dm import models as em_models
from exordos_core.vs.dm import models as models


class ValueNotBelongsToVariableError(ra_e.ValidationErrorException):
    message = "Value does not belong to the variable"


class NoValueSelectedError(ra_e.ValidationErrorException):
    message = "No value selected"


class ValuesStoreController(controllers.RoutesListController):
    __TARGET_PATH__ = "/v1/vs/"


class ProjectScopedController(
    iam_controllers.PolicyBasedWithoutProjectController,
    controllers.BaseResourceControllerPaginated,
):
    """A controller over entities a project shares with the others.

    A read reaches the shared entities and the caller's own ones, a write
    only the caller's own: an entity is shared because the project holding
    it publishes it, and publishing something is not handing it over. A
    caller without a project, an admin token included, is not narrowed.
    """

    def _shared(self):
        """Return the clause selecting the entities every project reads."""
        raise NotImplementedError()

    def _readable(self, filters):
        if not self._ctx_project_id:
            return filters

        return dm_filters.AND(
            filters,
            dm_filters.OR(
                self._shared(),
                {"project_id": dm_filters.EQ(self._ctx_project_id)},
            ),
        )

    def _enforce_own(self, uuid):
        """Refuse a write to an entity another project owns."""
        if not self._ctx_project_id:
            return

        self.model.objects.get_one(
            filters={
                "uuid": dm_filters.EQ(uuid),
                "project_id": dm_filters.EQ(self._ctx_project_id),
            },
        )

    def create(self, **kwargs):
        self._enforce_and_override_project_id_in_kwargs("create", kwargs)
        return super().create(**kwargs)

    def get(self, uuid, **kwargs):
        self._enforce("read")
        filters = dict(kwargs, uuid=dm_filters.EQ(uuid))
        return self.model.objects.get_one(filters=self._readable(filters))

    def filter(self, filters, order_by=None):
        return super().filter(self._readable(filters), order_by=order_by)

    def update(self, uuid, **kwargs):
        self._enforce_own(uuid)
        return super().update(uuid, **kwargs)

    def delete(self, uuid):
        self._enforce_own(uuid)
        return super().delete(uuid)


class ProfilesController(ProjectScopedController):
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

    def _shared(self):
        """A global profile is read by every project."""
        return {"profile_type": dm_filters.EQ(infra_c.ProfileType.GLOBAL.value)}

    @actions.post
    def activate(self, resource: models.Profile):
        self._enforce("activate")
        resource.activate()
        return resource


class VariablesController(ProjectScopedController):
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

    def _shared(self):
        """A variable an element publishes through its manifest exports."""
        exported = em_models.Export.exported_resource_uuids()
        return {"uuid": dm_filters.In(list(exported))}

    def update(self, uuid, **kwargs):
        kwargs["status"] = infra_c.VariableStatus.IN_PROGRESS.value
        return super().update(uuid, **kwargs)

    @actions.post
    def select_value(self, resource: models.Variable, value: str):
        self._enforce("select_value")
        self._enforce_own(resource.uuid)
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
        self._enforce_own(resource.uuid)
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
