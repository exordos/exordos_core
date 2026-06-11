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

from gcl_sdk.agents.universal.orch_api import routes as orch_routes
from gcl_sdk.agents.universal.status_api import routes as status_routes
from restalchemy.api import routes

from exordos_core.boot_api.api import controllers


class NetbootRoute(routes.Route):
    """Handler for /v1/boots/ endpoint"""

    __controller__ = controllers.NetBootController
    __allow_methods__ = [routes.GET]


class UniversalAgentsRoute(orch_routes.UniversalAgentsRoute):
    """Handler for /v1/agents/ endpoint"""

    __allow_methods__ = [
        routes.GET,
        routes.CREATE,
        routes.UPDATE,
        routes.DELETE,
    ]


class ApiEndpointRoute(routes.Route):
    """Handler for /v1/ endpoint"""

    __controller__ = controllers.ApiEndpointController
    __allow_methods__ = [routes.FILTER]

    nodes = routes.route(status_routes.NodesRoute)
    boots = routes.route(NetbootRoute)
    node_verifiers = routes.route(status_routes.NodeVerifiersRoute)
    agents = routes.route(UniversalAgentsRoute)
    kind = routes.route(status_routes.KindRoute)
