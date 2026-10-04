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

from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import patch
import uuid as sys_uuid

from exordos_core.storage.scheduler import service


def test_mds_cluster_is_scheduled_only_to_an_agent_on_the_core():
    core = sys_uuid.uuid4()
    core_agent = SimpleNamespace(uuid=sys_uuid.uuid4(), node=core)
    ost_agent = SimpleNamespace(uuid=sys_uuid.uuid4(), node=sys_uuid.uuid4())
    builder = SimpleNamespace(uuid=sys_uuid.uuid4())
    cluster = MagicMock()
    scheduler = service.StorageClusterSchedulerService.__new__(
        service.StorageClusterSchedulerService
    )
    with (
        patch.object(scheduler, "_get_unscheduled_clusters", return_value=[cluster]),
        patch.object(scheduler, "_get_cluster_builders", return_value=[builder]),
        patch.object(
            service.ua_models.UniversalAgent,
            "have_capabilities",
            return_value={"storage_cluster": [ost_agent, core_agent]},
        ),
        patch.object(service.ua_utils, "system_uuid", return_value=core),
        patch.object(service.contexts, "Context"),
    ):
        scheduler._iteration()
    assert cluster.agent == core_agent.uuid
    assert cluster.builder == builder.uuid
    cluster.update.assert_called_once()
