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

import logging
import uuid as sys_uuid

from gcl_looper.services.oslo import base as oslo_base
from gcl_sdk.agents.universal import utils as ua_utils
from gcl_sdk.agents.universal.clients.orch import db as orch_db
from gcl_sdk.agents.universal.services import agent as ua_agent_service

from exordos_core.repo.agents.universal.drivers import (
    repo_element as repo_element_driver,
)

LOG = logging.getLogger(__name__)


class RepoElementAgentService(
    ua_agent_service.UniversalAgentService, oslo_base.OsloConfigurableService
):
    """Repo element agent service managed by the launchpad.

    The service is a universal agent that manages repository elements
    through the repo proxy installed element capability driver. It exposes
    the `repo_proxy_installed_element` capability to the orchestrator and
    translates repo element resources into Elements Management manifests.
    """

    @classmethod
    def svc_from_config(cls, config_file: str) -> "RepoElementAgentService":
        """Create service instance from config file."""
        caps_driver = repo_element_driver.RepoElementCapabilityDriver()
        agent_uuid = sys_uuid.uuid5(
            ua_utils.system_uuid(),
            "repo_element_agent_service",
        )

        return cls(
            agent_uuid=agent_uuid,
            orch_client=orch_db.DatabaseOrchClient(),
            caps_drivers=[caps_driver],
            facts_drivers=[],
            iter_min_period=3,
            payload_path=None,
        )
