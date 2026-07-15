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
import typing as tp
import uuid as sys_uuid

from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.infra.dm import models as sdk_models
from gcl_sdk.paas.services import builder
from restalchemy.dm import filters as ra_filters
from restalchemy.storage import exceptions as ra_storage_exc

from exordos_core.elements import constants as cc
from exordos_core.network.border.dm import models
from exordos_core.network.lb.dm import models as lb_models

LOG = logging.getLogger(__name__)

NODE_SET_KIND = lb_models.TargetNodeSet.get_resource_kind()


class BorderBuilder(builder.PaaSBuilder):
    def __init__(
        self,
        instance_model: tp.Type[models.PaasBorder] = models.PaasBorder,
    ):
        super().__init__(instance_model)

    def create_paas_objects(
        self, instance: models.PaasBorder
    ) -> tp.Collection[ua_models.TargetResourceKindAwareMixin]:
        return self.actualize_paas_objects(
            instance, builder.PaaSCollection(paas_objects=tuple())
        )

    def actualize_paas_objects(
        self,
        instance: models.PaasBorder,
        paas_collection: builder.PaaSCollection,
    ) -> tp.Collection[ua_models.TargetResourceKindAwareMixin]:
        """Produce the border capability resource(s).

        Deployment shape precedence (see the user_api Border model):
        * ``instance.node``   -> ``border_node`` pinned to that node's agent
          (distributed egress, e.g. a managed realm node);
        * ``type.kind: core`` -> ``border_node`` on the dedicated VM the iaas
          builder provisions (net_border_iaas -> target_node_set);
        * otherwise           -> ``border_agent`` on the core node (step 0).
        """

        if instance.node:
            actual_resources = [
                models.BorderNode(
                    uuid=instance.uuid,
                    agent_uuid=instance.node,
                    snat_rules=instance.get_snat_rules(),
                    forwards=instance.get_forwards(),
                    routes=instance.get_routes(),
                )
            ]
        elif instance.type.kind == "core":
            # Until the iaas builder has the VM up there is nothing to
            # schedule; the instance stays IN_PROGRESS and is retried.
            actual_resources = [
                models.BorderNode(
                    uuid=sys_uuid.UUID(node_uuid),
                    agent_uuid=sys_uuid.UUID(node_uuid),
                    snat_rules=instance.get_snat_rules(),
                    forwards=instance.get_forwards(),
                    routes=instance.get_routes(),
                )
                for node_uuid in self._get_iaas_nodes(instance)
            ]
        else:
            actual_resources = [
                models.BorderAgent(
                    uuid=instance.uuid,
                    snat_rules=instance.get_snat_rules(),
                    forwards=instance.get_forwards(),
                    routes=instance.get_routes(),
                )
            ]

        if paas_collection.paas_objects and all(
            (p.actual and p.actual.status == "ACTIVE")
            for p in paas_collection.paas_objects
        ):
            instance.status = cc.ServiceStatus.ACTIVE.value
        elif paas_collection.paas_objects and any(
            (p.actual and p.actual.status == "ERROR")
            for p in paas_collection.paas_objects
        ):
            instance.status = cc.ServiceStatus.ERROR.value
        else:
            instance.status = cc.ServiceStatus.IN_PROGRESS.value

        return actual_resources

    def _get_iaas_nodes(self, instance: models.PaasBorder) -> tp.List[str]:
        """Node uuids of the border VM node set (empty until provisioned)."""
        try:
            res = ua_models.Resource.objects.get_one(
                filters={
                    "uuid": ra_filters.EQ(instance.uuid),
                    "kind": ra_filters.EQ(NODE_SET_KIND),
                }
            )
        except ra_storage_exc.RecordNotFound:
            return []
        nodeset = sdk_models.NodeSet.from_ua_resource(res)
        return list(nodeset.nodes or {})
