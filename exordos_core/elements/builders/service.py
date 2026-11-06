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
import uuid as sys_uuid

from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.paas.services import builder

from exordos_core.common.dm import targets
from exordos_core.elements import constants as cc
from exordos_core.elements.dm import res_models

LOG = logging.getLogger(__name__)


class CommonBuilder(builder.PaaSBuilder):
    @classmethod
    def service_uuid_by_node_path(
        cls, node_uuid: sys_uuid.UUID, path: str
    ) -> sys_uuid.UUID:
        return sys_uuid.uuid5(node_uuid, path)


class ServiceNodeBuilder(CommonBuilder):
    def __init__(
        self,
        instance_model: tp.Type[res_models.Service] = res_models.Service,
    ):
        super().__init__(instance_model)

    def _get_updated_instances(self):
        instances = {i.uuid: i for i in super()._get_updated_instances()}
        for instance in targets.changed_node_set_targets(
            self._instance_model,
            self._instance_model.get_resource_kind(),
            "service_agent_node",
            "instance.service_type->>'kind' IN ('monopoly', 'monopoly_oneshot')",
        ):
            instances[instance.uuid] = instance
        return tuple(instances.values())

    def create_paas_objects(
        self, instance: res_models.Service
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
        instance: res_models.Service,
        paas_collection: builder.PaaSCollection,
    ) -> tp.Collection[ua_models.TargetResourceKindAwareMixin]:
        """Basic update, all derivatives are non-unique"""

        actual_resources = []

        nodes = instance.target.target_nodes()

        for node_uuid in nodes:
            actual_resources.append(
                res_models.ServiceNode(
                    uuid=CommonBuilder.service_uuid_by_node_path(
                        node_uuid,
                        instance.path,
                    ),
                    agent_uuid=node_uuid,
                    name=instance.name,
                    path=instance.path,
                    user=instance.user,
                    group=instance.group,
                    service_type=instance.service_type,
                    before=[i.get_dp_obj() for i in instance.before],
                    after=[i.get_dp_obj() for i in instance.after],
                )
            )

            if instance.service_type.kind.startswith("monopoly"):
                # For monopoly services we need only one node scheduled
                break

        actuals = {p.target.uuid: p.actual for p in paas_collection.paas_objects}
        if all(
            actuals.get(r.uuid) and actuals[r.uuid].status == "ACTIVE"
            for r in actual_resources
        ):
            instance.status = cc.ServiceStatus.ACTIVE.value
        else:
            instance.status = cc.ServiceStatus.IN_PROGRESS.value

        return actual_resources
