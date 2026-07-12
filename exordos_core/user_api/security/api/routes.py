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

from gcl_sdk.audit.api import routes as audit_routes
from restalchemy.api import routes

from exordos_core.user_api.security.api import controllers


class RulesRoute(routes.Route):
    """Handler for /v1/security/rules/ endpoint"""

    __controller__ = controllers.RuleController


class SecurityRoute(routes.Route):
    """Handler for /v1/security/ endpoint"""

    __controller__ = controllers.SecurityController
    __allow_methods__ = [routes.FILTER]

    audit = routes.route(audit_routes.AuditRoute)
    rules = routes.route(RulesRoute)
