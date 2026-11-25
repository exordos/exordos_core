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
from gcl_sdk.agents.universal.status_api import controllers as ua_controllers
from restalchemy.api import controllers

from exordos_core.common import ua_access


class ApiEndpointController(controllers.RoutesListController):
    """Controller for /v1/ endpoint"""

    __TARGET_PATH__ = "/v1/"


class UniversalAgentsController(ua_controllers.UniversalAgentsController):
    def create(self, **kwargs):
        if "node" in kwargs:
            ua_access.require_request_node(self._req.context, kwargs["node"])
        return super().create(**kwargs)

    def get(self, uuid, **kwargs):
        agent = super().get(uuid, **kwargs)
        ua_access.require_request_node(self._req.context, agent.node)
        return agent

    def update(self, uuid, **kwargs):
        if "node" in kwargs:
            ua_access.require_request_node(self._req.context, kwargs["node"])
        return super().update(uuid, **kwargs)
