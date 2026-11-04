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

from gcl_iam.api import controllers as iam_controllers
from restalchemy.api import controllers
from restalchemy.api import packers
from restalchemy.api import resources
from restalchemy.storage import exceptions

from exordos_core.config import constants as cc
from exordos_core.config.dm import models as conf_models


class ConfigController(controllers.RoutesListController):
    __TARGET_PATH__ = "/v1/config/"


class ConfigPacker(packers.JSONPacker):
    def pack_resource(self, obj):
        value = super().pack_resource(obj)
        if obj.is_provider_bootstrap():
            content = obj.body.render()
            header = content.splitlines()[0] if content else ""
            value["body"] = {
                "kind": "text",
                "content": header if header.startswith("# exordos-provider ") else "",
            }
            value["on_change"] = {"kind": "no_action"}
        return value


class ConfigsController(iam_controllers.PolicyBasedController):
    """Controller for /v1/config/configs/ endpoint"""

    __policy_name__ = "config"
    __policy_service_name__ = "config"
    __packer__ = ConfigPacker

    __resource__ = resources.ResourceByRAModel(
        model_class=conf_models.Config,
        process_filters=True,
        convert_underscore=False,
    )

    def update(self, uuid, **kwargs):
        project_filters = {}
        self._enforce_and_override_project_id_in_kwargs("update", project_filters)
        config = controllers.BaseResourceController.get(self, uuid, **project_filters)
        if config.is_provider_bootstrap():
            raise exceptions.ConflictRecords(
                model="Config", msg="provider bootstrap is managed through its node"
            )
        # Force config to be NEW
        # In order to regenerate renders
        kwargs["status"] = cc.ConfigStatus.NEW.value

        return super().update(uuid, **kwargs)
