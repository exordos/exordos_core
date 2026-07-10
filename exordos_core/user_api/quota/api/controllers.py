#    Copyright 2026 Genesis Corporation.
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
from restalchemy.api import constants
from restalchemy.api import controllers as ra_controllers
from restalchemy.api import resources
from restalchemy.openapi import constants as oa_c
from restalchemy.openapi import utils
import webob

from exordos_core.quota.dm import models


class SummaryController(ra_controllers.Controller):
    @utils.extend_schema(
        summary="Get reservation summary",
        parameters=[
            oa_c.build_openapi_parameter(
                name="project_id",
                openapi_type="string",
                param_type="project_id",
                required=False,
            ),
        ],
        responses=oa_c.build_openapi_object_response({}),
    )
    def filter(self, project_id: str | None = None, *args, **kwargs) -> list:
        return models.QuotaLimit.get_project_quota_summary(project_id)

    def process_result(
        self,
        result: tp.Union[dict, list],
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


class QuotaController(ra_controllers.RoutesListController):
    __TARGET_PATH__ = "/v1/quota/"


class QuotaLimitController(
    iam_controllers.PolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    __policy_service_name__ = "quota"
    __policy_name__ = "limit"

    __resource__ = resources.ResourceByRAModel(
        models.QuotaLimit,
        process_filters=True,
        convert_underscore=False,
    )
