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

from gcl_looper.services.oslo import base as oslo_base
from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.agents.universal.services import builder as sdk_builder

from exordos_core.repo.dm import models

LOG = logging.getLogger(__name__)


class RepoElement(models.RepoElement, ua_models.InstanceMixin):
    @classmethod
    def get_resource_kind(cls) -> str:
        return "repo_proxy_element"


class RepoElementBuilderService(
    sdk_builder.UniversalBuilderService,
    oslo_base.OsloConfigurableService,
):
    def __init__(
        self,
        iter_min_period: int = 3,
        iter_pause: float = 0.1,
    ) -> None:
        super().__init__(
            RepoElement,
            iter_min_period=iter_min_period,
            iter_pause=iter_pause,
        )
