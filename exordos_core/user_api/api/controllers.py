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

import logging

from restalchemy.api import controllers
from restalchemy.common import contexts

from exordos_core.common import constants as c
from exordos_core.common.dm import models as common_models

LOG = logging.getLogger(__name__)


class OwnedTagsControllerMixin:
    """Stamps the caller's identity onto the tags of a created row.

    Mix in ahead of the resource controller of a model based on
    `ModelWithReservedTags`. The tag is what tells a reconciler its own
    rows from everybody else's, so the client cannot write it: reserved
    tags are dropped from the request and the caller's own is added.

    A caller with no subject of its own leaves the row unowned, which
    every reconciler reads as "not mine to remove".
    """

    def _caller_owner_tag(self):
        introspection = contexts.get_context().iam_context.introspection_info()
        user_uuid = ((introspection or {}).get("user_info") or {}).get("uuid")
        return c.owner_user_tag(user_uuid) if user_uuid else None

    def create(self, **kwargs):
        tags = common_models.client_tags(kwargs.get("tags"))
        owner = self._caller_owner_tag()
        if owner:
            tags = tags + [owner]
        kwargs["tags"] = tags
        return super().create(**kwargs)


class ApiEndpointController(controllers.RoutesListController):
    """Controller for /v1/ endpoint"""

    __TARGET_PATH__ = "/v1/"


class HealthController(controllers.Controller):
    """Controller for /v1/health/ endpoint"""

    def filter(self, filters, **kwargs):
        return "OK"
