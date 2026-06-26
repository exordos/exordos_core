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

from exordos_core.user_api.api import controllers
from exordos_core.user_api.compute.api import routes as compute_routes
from exordos_core.user_api.config.api import routes as config_routes
from exordos_core.user_api.dns.api import routes as dns_routes
from exordos_core.user_api.em.api import routes as em_routes
from exordos_core.user_api.iam.api import routes as iam_routes
from exordos_core.user_api.network.api import routes as network_routes
from exordos_core.user_api.secret.api import routes as secret_routes
from exordos_core.user_api.security.api import routes as security_routes
from exordos_core.user_api.ua import routes as ua_routes
from exordos_core.user_api.vs.api import routes as vs_routers


# TODO(e.frolov): should be raw route
class HealthRoute(routes.Route):
    """Handler for /v1/health endpoint"""

    __controller__ = controllers.HealthController
    __allow_methods__ = [routes.FILTER]


class ApiEndpointRoute(routes.Route):
    """Handler for /v1/ endpoint"""

    __controller__ = controllers.ApiEndpointController
    __allow_methods__ = [routes.FILTER]

    dns = routes.route(dns_routes.DnsRoute)
    health = routes.route(HealthRoute)
    iam = routes.route(iam_routes.IamRoute)
    em = routes.route(em_routes.ElementManagerRoute)
    vs = routes.route(vs_routers.VSRoute)
    config = routes.route(config_routes.ConfigRoute)
    secret = routes.route(secret_routes.SecretRoute)
    security = routes.route(security_routes.SecurityRoute)
    compute = routes.route(compute_routes.ComputeRoute)
    network = routes.route(network_routes.NetworkRoute)
    ua = routes.route(ua_routes.UaRoute)
