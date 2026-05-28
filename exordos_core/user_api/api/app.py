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

from gcl_iam import middlewares as iam_mw
from restalchemy.api import applications
from restalchemy.api import middlewares
from restalchemy.api import routes
from restalchemy.api.middlewares import logging as logging_mw
from restalchemy.openapi import engines as openapi_engines
from restalchemy.openapi import structures as openapi_structures

from exordos_core import version
from exordos_core.common import contexts as common_contexts
from exordos_core.common.api.middlewares import errors as errors_mw
from exordos_core.user_api.api import middlewares as user_api_mw
from exordos_core.user_api.api import routes as app_routes
from exordos_core.user_api.api import versions

skip_auth_endpoints = []
readonly_whitelist = {"GET": "^\/.?"}


class UserApiApp(routes.RootRoute):
    pass


# Route to /v1/ endpoint.
setattr(
    UserApiApp,
    versions.API_VERSION_v1,
    routes.route(app_routes.ApiEndpointRoute),
)


def get_api_application():
    return UserApiApp


def get_openapi_engine():
    openapi_engine = openapi_engines.OpenApiEngine(
        info=openapi_structures.OpenApiInfo(
            title=f"Exordos Core {versions.API_VERSION_v1} User API",
            version=version.version_info,
            description=f"OpenAPI - Exordos Core {versions.API_VERSION_v1}",
        ),
        paths=openapi_structures.OpenApiPaths(),
        components=openapi_structures.OpenApiComponents(),
    )
    return openapi_engine


def build_wsgi_application(context_storage, iam_engine_driver):
    return middlewares.attach_middlewares(
        applications.OpenApiApplication(
            route_class=get_api_application(),
            openapi_engine=get_openapi_engine(),
        ),
        [
            user_api_mw.SecurityRulesMiddleware,
            middlewares.configure_middleware(
                iam_mw.GenesisCoreAuthMiddleware,
                # service_name="iam",
                context_kwargs={
                    "context_storage": context_storage,
                },
                context_class=common_contexts.GenesisCoreAuthContext,
                iam_engine_driver=iam_engine_driver,
                readonly_whitelist=readonly_whitelist,
                skip_auth_endpoints=skip_auth_endpoints,
            ),
            errors_mw.ErrorsHandlerMiddleware,
            logging_mw.LoggingMiddleware,
        ],
    )
