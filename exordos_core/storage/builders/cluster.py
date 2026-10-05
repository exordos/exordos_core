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
from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.agents.universal.services import builder as sdk_builder
from gcl_sdk.agents.universal.services import common as sdk_svc_common

from exordos_core.storage import state
from exordos_core.storage.dm import models as storage_models


class StorageClusterBuilderService(sdk_builder.CollectionUniversalBuilderService):
    """Reconcile MDS clusters and OST nodes on their assigned agents.

    Ready OST reports update MDS topology. Removed OSTs remain running until
    MDS has acknowledged the new topology. Storage operations share the same
    transaction lock as disk admission.
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
            instance_models=(storage_models.Cluster, storage_models.Node),
            service_spec=svc_spec,
            iter_min_period=iter_min_period,
            iter_pause=iter_pause,
        )

    def prepare_iteration(self) -> tp.Dict[str, tp.Any]:
        """Perform actions before iteration and return the iteration context.

        The result is a dictionary that is passed to the iteration context.
        """
        with storage_models.StorageCluster._get_engine().session_manager() as session:
            state.lock(session)
        return {"clause_filters": {"builder": self.ua_service_spec.uuid}}

    def actualize_outdated_instance(
        self,
        current_instance: storage_models.Cluster | storage_models.Node,
        actual_instance: storage_models.Cluster | storage_models.Node,
    ) -> None:
        """Sync cluster capacity or admit an OST with matching ready configuration."""
        if isinstance(current_instance, storage_models.Node):
            matches = all(
                getattr(current_instance, field) == getattr(actual_instance, field)
                for field in current_instance.get_resource_target_fields()
            )
            current_instance.status = (
                actual_instance.status if matches else "IN_PROGRESS"
            )
            current_instance.update()
            cluster = storage_models.StorageCluster.objects.get_one(
                filters={"uuid": current_instance.cluster}
            )
            state.sync_cluster(cluster)
            return
        current_instance.storage_pools = actual_instance.storage_pools
        current_instance.capacity_info = actual_instance.capacity_info
        current_instance.status = actual_instance.status

    def can_delete_instance_resource(self, resource):
        if resource.kind != "storage_node":
            return super().can_delete_instance_resource(resource)
        # Keep the OST running until the MDS has acknowledged its removal.
        actual = ua_models.Resource.objects.get_all(
            filters={"uuid": resource.value["cluster"], "kind": "storage_cluster"}
        )
        target = ua_models.TargetResource.objects.get_all(
            filters={"uuid": resource.value["cluster"], "kind": "storage_cluster"}
        )
        return (
            bool(actual)
            and bool(target)
            and actual[0].hash == target[0].hash
            and str(resource.uuid)
            not in target[0].value["driver_spec"].get("nodes", {})
        )

    def _model_iteration(self):
        # Collection builders open a separate transaction for each model.
        with storage_models.StorageCluster._get_engine().session_manager() as session:
            state.lock(session)
            super()._model_iteration()
