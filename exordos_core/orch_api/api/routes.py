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
from restalchemy.api import routes

from exordos_core.orch_api.api import controllers


class UniversalAgentsGetPayloadAction(orch_routes.UniversalAgentsGetPayloadAction):
    __controller__ = controllers.UniversalAgentsController


class UniversalAgentsRoute(orch_routes.UniversalAgentsRoute):
    __controller__ = controllers.UniversalAgentsController
    get_payload = routes.action(UniversalAgentsGetPayloadAction)


class ApiEndpointRoute(routes.Route):
    """Handler for /v1/ endpoint"""

    __controller__ = controllers.ApiEndpointController
    __allow_methods__ = [routes.FILTER]

    agents = routes.route(UniversalAgentsRoute)
