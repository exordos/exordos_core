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
import typing as tp

from gcl_looper.services.oslo import base as oslo_base
from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.agents.universal.services import builder as sdk_builder

from exordos_core.repo.dm import models

LOG = logging.getLogger(__name__)


class Repository(models.Repository, ua_models.InstanceMixin):
    @classmethod
    def get_resource_kind(cls) -> str:
        return "repo_proxy_repository"


class RepoProxyBuilderService(
    sdk_builder.UniversalBuilderService,
    oslo_base.OsloConfigurableService,
):
    def __init__(
        self,
        iter_min_period: int = 3,
        iter_pause: float = 0.1,
    ) -> None:
        super().__init__(
            Repository,
            iter_min_period=iter_min_period,
            iter_pause=iter_pause,
        )

    def post_create_instance_resource(
        self,
        instance: Repository,
        resource: ua_models.TargetResource,
        derivatives: tp.Collection[ua_models.TargetResource] = tuple(),
    ) -> None:
        """The hook is performed after saving instance resource.

        The hook is called only for new instances.
        """
        super().post_create_instance_resource(instance, resource, derivatives)
        elements = instance.get_elements_from_inventory()
        LOG.debug(
            "Fetched %d elements from repository %s", len(elements), instance.name
        )

        # Save the elements to the database
        for element in elements:
            element.save()
