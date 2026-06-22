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

import json
import typing as tp

from gcl_iam.api import controllers as iam_controllers
from restalchemy.api import actions
from restalchemy.api import constants
from restalchemy.api import controllers
from restalchemy.api import resources
from restalchemy.common import exceptions as ra_e
from restalchemy.dm import filters as dm_filters
from restalchemy.openapi import constants as oa_c
from restalchemy.openapi import utils
import webob

from exordos_core.elements.dm import models
from exordos_core.vs.dm import models as vs_models


class ElementHasNoProfileError(ra_e.ValidationErrorException):
    message = "Element has no profile"


class ElementManagerController(controllers.RoutesListController):
    __TARGET_PATH__ = "/v1/em/"


class SchemaController(controllers.Controller):
    @utils.extend_schema(
        summary="Get manifest schema",
        parameters=[],
        responses=oa_c.build_openapi_object_response({}),
    )
    def filter(self, *args, **kwargs) -> dict:
        models.element_engine.load_schemas()
        return models.element_engine.full_schema

    def process_result(
        self,
        result: dict,
        status_code: int = 200,
        headers: tp.Optional[dict] = None,
        add_location: bool = False,
    ) -> webob.Response:
        if headers is not None:
            headers["Content-Type"] = constants.CONTENT_TYPE_APPLICATION_JSON
        else:
            headers = {"Content-Type": constants.CONTENT_TYPE_APPLICATION_JSON}
        return webob.Response(
            body=json.dumps(result).encode(),
            status=status_code,
            content_type=constants.CONTENT_TYPE_APPLICATION_JSON,
            headerlist=[(k, v) for k, v in headers.items()] if headers else [],
        )


class ManifestController(
    iam_controllers.PolicyBasedWithoutProjectController,
    controllers.BaseResourceControllerPaginated,
):
    __policy_service_name__ = "em"
    __policy_name__ = "manifest"
    __resource__ = resources.ResourceByRAModel(
        model_class=models.Manifest,
        convert_underscore=False,
        hidden_fields=resources.HiddenFieldMap(
            update=["status", "created_at", "updated_at"],
        ),
    )

    @actions.post
    def install(self, resource):
        manifest, _ = resource.install()
        return manifest

    @actions.post
    def upgrade(self, resource):
        manifest, _ = resource.upgrade()
        return manifest

    @actions.post
    def uninstall(self, resource):
        return resource.uninstall()

    @actions.get
    def validate(self, resource):
        return resource.validate_schema_base()


class ElementController(
    iam_controllers.PolicyBasedWithoutProjectController,
    controllers.BaseResourceControllerPaginated,
):
    __policy_service_name__ = "em"
    __policy_name__ = "element"
    __resource__ = resources.ResourceByModelWithCustomProps(
        model_class=models.Element,
        convert_underscore=False,
        hidden_fields=resources.HiddenFieldMap(
            update=[
                "status",
                "created_at",
                "updated_at",
                "name",
                "version",
            ],
        ),
    )

    @actions.post
    def set_profile(self, resource: models.Element, profile: str):
        profile = vs_models.Profile.objects.get_one(
            filters={"uuid": dm_filters.EQ(profile)},
        )
        resource.profile = profile
        resource.update()

        # Need to update force to rebuild the variables
        # related to the profile
        profile.update(force=True)
        return resource

    @actions.post
    def clear_profile(self, resource: models.Element):
        if not resource.profile:
            raise ElementHasNoProfileError()

        profile = resource.profile
        resource.profile = None
        resource.update()

        # Need to update force to rebuild the variables
        # related to the profile
        profile.update(force=True)
        return resource


class ElementResourceController(
    # iam_controllers.PolicyBasedWithoutProjectController,  # nested
    controllers.BaseNestedResourceControllerPaginated,
):
    __pr_name__ = "element"
    # __policy_service_name__ = "em"
    # __policy_name__ = "resource"
    __resource__ = resources.ResourceByModelWithCustomProps(
        model_class=models.Resource,
        convert_underscore=False,
        hidden_fields=resources.HiddenFieldMap(
            filter=[
                "target_resource",
                "actual_resource",
            ],
            get=[
                "target_resource",
                "actual_resource",
            ],
            create=[
                "target_resource",
                "actual_resource",
            ],
            update=[
                "target_resource",
                "actual_resource",
                "status",
                "created_at",
                "updated_at",
                "name",
                "version",
            ],
        ),
    )

    def __init__(self, *args, **kwargs):
        models.element_engine.load_from_database()
        super().__init__(*args, **kwargs)


class ResourceAllController(iam_controllers.PolicyBasedController):
    """Controller for /v1/resources/ endpoint"""

    __resource__ = resources.ResourceByModelWithCustomProps(
        model_class=models.Resource,
        convert_underscore=False,
        hidden_fields=resources.HiddenFieldMap(
            filter=[
                "target_resource",
                "actual_resource",
            ],
            get=[
                "target_resource",
                "actual_resource",
            ],
            create=[
                "target_resource",
                "actual_resource",
            ],
            update=[
                "target_resource",
                "actual_resource",
                "status",
                "created_at",
                "updated_at",
                "name",
                "version",
            ],
        ),
    )

    def __init__(self, *args, **kwargs):
        models.element_engine.load_from_database()
        super().__init__(*args, **kwargs)


class ElementExportController(
    controllers.BaseNestedResourceControllerPaginated,
):
    __pr_name__ = "element"
    __resource__ = resources.ResourceByRAModel(
        model_class=models.Export,
        process_filters=True,
        convert_underscore=False,
    )

    def __init__(self, *args, **kwargs):
        models.element_engine.load_from_database()
        super().__init__(*args, **kwargs)


class ExportAllController(iam_controllers.PolicyBasedController):
    """Controller for /v1/exports/ endpoint"""

    # NOTE(slashburygin): we need it here because in restalchemy we can add model to resources only once
    __resource__ = resources.ResourceMap.model_type_to_resource[models.Export]

    def __init__(self, *args, **kwargs):
        models.element_engine.load_from_database()
        super().__init__(*args, **kwargs)


class ElementImportController(
    controllers.BaseNestedResourceControllerPaginated,
):
    __pr_name__ = "element"
    __resource__ = resources.ResourceByModelWithCustomProps(
        model_class=models.Import,
        process_filters=True,
        convert_underscore=False,
    )

    def __init__(self, *args, **kwargs):
        models.element_engine.load_from_database()
        super().__init__(*args, **kwargs)


class ImportAllController(iam_controllers.PolicyBasedController):
    """Controller for /v1/imports/ endpoint"""

    # NOTE(slashburygin): we need it here because in restalchemy we can add model to resources only once
    __resource__ = resources.ResourceMap.model_type_to_resource[models.Import]

    def __init__(self, *args, **kwargs):
        models.element_engine.load_from_database()
        super().__init__(*args, **kwargs)


class ServicesController(iam_controllers.PolicyBasedController):
    """Controller for /v1/em/services/ endpoint"""

    __policy_name__ = "em"
    __policy_service_name__ = "service"

    __resource__ = resources.ResourceByRAModel(
        model_class=models.Service,
        process_filters=True,
        convert_underscore=False,
    )
