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

from exordos_core.common import constants
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
        self._membership_cursor = constants.ZERO_UUID

    def _get_updated_instances(self):
        instances = {
            instance.uuid: instance for instance in super()._get_updated_instances()
        }
        model = self._instance_model
        rows = model._execute_expression(
            f"""
            SELECT service.* FROM {model.__tablename__} AS service
            JOIN ua_target_resources AS master
              ON master.uuid = service.uuid AND master.kind = %s
            CROSS JOIN LATERAL (
                SELECT ARRAY(
                    SELECT child.agent FROM ua_target_resources AS child
                    WHERE child.master = service.uuid AND child.kind = %s
                ) AS agents
            ) AS current
            CROSS JOIN LATERAL (
                SELECT ARRAY(
                    SELECT node.uuid FROM nodes AS node
                    WHERE node.node_set = (service.target->>'node_set')::uuid
                    ORDER BY node.uuid = ANY(current.agents) DESC, node.uuid
                    LIMIT CASE WHEN service.service_type->>'kind'
                        IN ('monopoly', 'monopoly_oneshot') THEN 1 END
                ) AS agents
            ) AS desired
            WHERE service.target->>'kind' = 'node_set'
              AND service.uuid > %s
              AND NOT (current.agents @> desired.agents
                       AND desired.agents @> current.agents
                       AND cardinality(current.agents) = cardinality(desired.agents))
            ORDER BY service.uuid
            LIMIT %s
            """,
            (
                model.get_resource_kind(),
                res_models.ServiceNode.get_resource_kind(),
                self._membership_cursor,
                constants.DEFAULT_SQL_LIMIT,
            ),
        )
        members = model.restore_many_from_storage(rows)
        self._membership_cursor = (
            members[-1].uuid
            if len(members) == constants.DEFAULT_SQL_LIMIT
            else constants.ZERO_UUID
        )
        for instance in members:
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
        actuals = {
            pair.target.uuid: pair.actual for pair in paas_collection.paas_objects
        }
        if instance.service_type.kind.startswith("monopoly"):
            nodes = sorted(
                nodes,
                key=lambda node: (
                    self.service_uuid_by_node_path(node, instance.path) not in actuals,
                    node,
                ),
            )[:1]

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

        if actual_resources and all(
            actuals.get(resource.uuid) and actuals[resource.uuid].status == "ACTIVE"
            for resource in actual_resources
        ):
            instance.status = cc.ServiceStatus.ACTIVE.value
        else:
            instance.status = cc.ServiceStatus.IN_PROGRESS.value

        return actual_resources
