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
from gcl_sdk.agents.universal.api import crypto as ua_crypto
from gcl_sdk.agents.universal.dm import models as ua_models
from restalchemy.api import actions
from restalchemy.api import controllers as ra_controllers
from restalchemy.api import resources
from restalchemy.dm import filters as dm_filters
from restalchemy.storage import exceptions as ra_storage_exceptions


class InternalController(ra_controllers.RoutesListController):
    __TARGET_PATH__ = "/v1/ua/"


class AgentController(
    iam_controllers.PolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    __policy_service_name__ = "agent"
    __policy_name__ = "ua"

    __resource__ = resources.ResourceByRAModel(
        ua_models.UniversalAgent,
        convert_underscore=False,
    )

    @actions.post
    def issue_key(self, resource: ua_models.UniversalAgent):
        self._enforce("issue_key")

        try:
            enc_key = ua_models.NodeEncryptionKey.objects.get_one(
                filters={"uuid": dm_filters.EQ(resource.node)}
            )
        except ra_storage_exceptions.RecordNotFound:
            _, key_base64 = ua_crypto.generate_key_base64()
            enc_key = ua_models.NodeEncryptionKey(
                uuid=resource.node,
                private_key=key_base64,
            )
            enc_key.insert()

        return {"key": enc_key.private_key}


class ResourceController(
    iam_controllers.PolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    __policy_service_name__ = "resource"
    __policy_name__ = "ua"

    __resource__ = resources.ResourceByRAModel(
        ua_models.Resource,
        convert_underscore=False,
        hidden_fields=[
            "value",
        ],
    )


class TargetResourceController(
    iam_controllers.PolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    __policy_service_name__ = "target_resource"
    __policy_name__ = "ua"

    __resource__ = resources.ResourceByRAModel(
        ua_models.TargetResource,
        convert_underscore=False,
        hidden_fields=[
            "value",
        ],
    )
