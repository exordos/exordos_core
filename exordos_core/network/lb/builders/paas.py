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
import uuid

from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.infra.dm import models as sdk_models
from gcl_sdk.paas.services import builder
from restalchemy.dm import filters as ra_filters

from exordos_core.elements import constants as cc
from exordos_core.network.lb.dm import models

LOG = logging.getLogger(__name__)


class LBBuilder(builder.PaaSBuilder):
    def __init__(
        self,
        instance_model: tp.Type[models.PaasLB] = models.PaasLB,
    ):
        super().__init__(instance_model)

    def create_paas_objects(
        self, instance: models.PaasLB
    ) -> tp.Collection[ua_models.TargetResourceKindAwareMixin]:
        """Create a list of PaaS objects.

        The method returns a list of PaaS objects that are required
        for the instance.
        """

        return self.actualize_paas_objects(
            instance, builder.PaaSCollection(paas_objects=tuple())
        )

    def actualize_paas_objects(
        self,
        instance: models.PaasLB,
        paas_collection: builder.PaaSCollection,
    ) -> tp.Collection[ua_models.TargetResourceKindAwareMixin]:
        """Basic update, all derivatives are non-unique"""

        actual_resources = []

        vhosts = instance.get_vhosts()
        backend_pools = instance.get_backend_pools()

        if instance.type.kind == "core":
            nodes = self.get_actual_nodeset(instance).nodes
            for node_uuid in nodes.keys():
                nuuid = uuid.UUID(node_uuid)
                actual_resources.append(
                    models.PaasLBNode(
                        uuid=nuuid,
                        agent_uuid=nuuid,
                        vhosts=vhosts,
                        backend_pools=backend_pools,
                    )
                )
        elif instance.type.kind == "node":
            # The LB uuid, not the node's: several LBs may share one node.
            actual_resources.append(
                models.PaasLBNode(
                    uuid=instance.uuid,
                    agent_uuid=instance.type.node,
                    vhosts=vhosts,
                    backend_pools=backend_pools,
                )
            )
        elif instance.type.kind == "core_agent":
            actual_resources.append(
                models.PaasLBAgent(
                    uuid=instance.uuid,
                    vhosts=vhosts,
                    backend_pools=backend_pools,
                )
            )

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

    def get_actual_nodeset(self, instance):
        res = ua_models.Resource.objects.get_one(
            filters={
                "uuid": ra_filters.EQ(instance.uuid),
                "kind": ra_filters.EQ("target_node_set"),
            }
        )
        return sdk_models.NodeSet.from_ua_resource(res)
