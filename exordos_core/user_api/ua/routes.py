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

from restalchemy.api import routes

from exordos_core.user_api.ua import controllers


class AgentsRoute(routes.Route):
    """Handler for /v1/ua/agents/ endpoint"""

    __allow_methods__ = [routes.FILTER, routes.GET]
    __controller__ = controllers.AgentController


class ResourcesRoute(routes.Route):
    """Handler for /v1/ua/resources/ endpoint"""

    __allow_methods__ = [routes.FILTER, routes.GET]
    __controller__ = controllers.ResourceController


class TargetResourcesRoute(routes.Route):
    """Handler for /v1/ua/target_resources/ endpoint"""

    __allow_methods__ = [routes.FILTER, routes.GET]
    __controller__ = controllers.TargetResourceController


class UaRoute(routes.Route):
    """Handler for /v1/ua/ endpoint"""

    __controller__ = controllers.InternalController
    __allow_methods__ = [routes.FILTER]

    agents = routes.route(AgentsRoute)
    resources = routes.route(ResourcesRoute)
    target_resources = routes.route(TargetResourcesRoute)
