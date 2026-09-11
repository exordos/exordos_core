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

import typing as tp
import uuid as sys_uuid

from gcl_sdk.agents.universal.clients.orch import base as orch_base
from gcl_sdk.agents.universal.services import builder as sdk_builder
from gcl_sdk.agents.universal.services import common as sdk_svc_common

from exordos_core.storage.dm import models as storage_models


class StorageClusterBuilderService(sdk_builder.CollectionUniversalBuilderService):
    """Builder for StorageCluster resources.

    Much simpler than PoolBuilderService: a storage cluster has no
    machine/volume derivatives to create or update - it only ever needs
    its reported capacity synced from the agent, so every hook besides
    `prepare_iteration`/`actualize_outdated_instance` uses the base
    class's sensible defaults (always creatable/updatable, no
    derivatives).
    """

    def __init__(
        self,
        uuid: sys_uuid.UUID,
        orch_client: orch_base.AbstractOrchClient,
        iter_min_period: int = 1,
        iter_pause: float = 0.1,
    ) -> None:
        svc_spec = sdk_svc_common.UAServiceSpec(
            uuid=uuid,
            orch_client=orch_client,
            capabilities=("builder_storage_cluster",),
            name=f"storage_cluster_builder {str(uuid)[:8]}",
        )

        super().__init__(
            instance_models=(storage_models.Cluster,),
            service_spec=svc_spec,
            iter_min_period=iter_min_period,
            iter_pause=iter_pause,
        )

    def prepare_iteration(self) -> tp.Dict[str, tp.Any]:
        """Perform actions before iteration and return the iteration context.

        The result is a dictionary that is passed to the iteration context.
        """
        return {"clause_filters": {"builder": self.ua_service_spec.uuid}}

    def actualize_outdated_instance(
        self,
        current_instance: storage_models.Cluster,
        actual_instance: storage_models.Cluster,
    ) -> None:
        """Sync the capacity the cluster's own agent reported."""
        current_instance.storage_pools = actual_instance.storage_pools
        current_instance.status = actual_instance.status
