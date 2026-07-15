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

from exordos_core.user_api.network.api import controllers


class VhostRouteRoute(routes.Route):
    """Handler for /v1/network/lb/<uuid>/vhosts/<uuid>/routes/ endpoint"""

    __controller__ = controllers.VhostRouteController


class VhostRoute(routes.Route):
    """Handler for /v1/network/lb/<uuid>/vhosts/ endpoint"""

    __controller__ = controllers.VhostController

    routes = routes.route(VhostRouteRoute, resource_route=True)


class BackendPoolRoute(routes.Route):
    """Handler for /v1/network/lb/<uuid>/backend_pools/ endpoint"""

    __controller__ = controllers.BackendPoolController


class LBRoute(routes.Route):
    """Handler for /v1/network/lb/ endpoint"""

    __controller__ = controllers.LBController

    vhosts = routes.route(VhostRoute, resource_route=True)
    backend_pools = routes.route(BackendPoolRoute, resource_route=True)


class BorderRoute(routes.Route):
    """Handler for /v1/network/border/ endpoint.

    SNAT/DNAT rules are carried inline on the Border resource, so there are
    no nested sub-collections.
    """

    __controller__ = controllers.BorderController


class NetworkRoute(routes.Route):
    """Handler for /v1/network/ endpoint"""

    __controller__ = controllers.NetworkController
    __allow_methods__ = [routes.FILTER]

    lb = routes.route(LBRoute)
    border = routes.route(BorderRoute)
