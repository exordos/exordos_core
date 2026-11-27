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

import typing as tp
import uuid as sys_uuid

from restalchemy.dm import filters as dm_filters
from restalchemy.dm import models
from restalchemy.dm import properties
from restalchemy.dm import types
from restalchemy.dm import types_dynamic

from exordos_core.common import constants
from exordos_core.compute.dm import models as nm


def changed_node_set_targets(
    model, resource_kind, derivative_kind, singleton_condition="FALSE"
):
    """Find existing targets whose assigned agents differ from set membership.

    The table and optional singleton SQL condition come from the caller's
    model definition; resource kinds are bound parameters.
    """
    rows = model._execute_expression(
        f"""
        SELECT instance.* FROM {model.__tablename__} AS instance
        JOIN ua_target_resources AS master
          ON master.uuid = instance.uuid AND master.kind = %s
        CROSS JOIN LATERAL (
            SELECT ARRAY(
                SELECT child.agent FROM ua_target_resources AS child
                WHERE child.master = instance.uuid AND child.kind = %s
            ) AS agents
        ) AS current
        CROSS JOIN LATERAL (
            SELECT ARRAY(
                SELECT node.uuid FROM nodes AS node
                WHERE node.node_set = (instance.target->>'node_set')::uuid
                ORDER BY node.uuid = ANY(current.agents) DESC, node.uuid
                LIMIT CASE WHEN {singleton_condition} THEN 1 ELSE NULL END
            ) AS agents
        ) AS desired
        WHERE instance.target->>'kind' = 'node_set'
          AND NOT (current.agents @> desired.agents
                   AND desired.agents @> current.agents
                   AND cardinality(current.agents) = cardinality(desired.agents))
        ORDER BY instance.uuid
        LIMIT %s
        """,
        (resource_kind, derivative_kind, constants.DEFAULT_SQL_LIMIT),
    )
    return model.restore_many_from_storage(rows)


class AbstractTarget(types_dynamic.AbstractKindModel, models.SimpleViewMixin):
    def target_nodes(self) -> tp.List[sys_uuid.UUID]:
        """Returns list of target nodes where config should be deployed."""
        return []

    def owners(self) -> tp.List[sys_uuid.UUID]:
        """Return list of owners objects where config bind to.

        For instance, the simplest case if an ordinary node config.
        In that case, the owner and target is the node itself.
        A more complex case is when a config is bound to a node set.
        In this case the owner is the set and the targets are all nodes
        in this set.
        """
        return []

    def are_owners_alive(self) -> bool:
        raise NotImplementedError()


class NodeTarget(AbstractTarget):
    KIND = "node"

    node = properties.property(types.UUID(), required=True)

    @classmethod
    def from_node(cls, node: sys_uuid.UUID) -> "NodeTarget":
        return cls(node=node)

    def target_nodes(self) -> tp.List[sys_uuid.UUID]:
        return [self.node]

    def owners(self) -> tp.List[sys_uuid.UUID]:
        """It's the simplest case with an ordinary node config.

        In that case, the owner and target is the node itself.
        If owners are deleted, the config will be deleted as well.
        """
        return [self.node]

    def _fetch_nodes(self) -> tp.List[nm.Node]:
        return nm.Node.objects.get_all(filters={"uuid": str(self.node)})

    def are_owners_alive(self) -> bool:
        return bool(self._fetch_nodes())


class NodeSetTarget(AbstractTarget):
    KIND = "node_set"

    node_set = properties.property(types.UUID(), required=True)

    @classmethod
    def from_node_set(cls, node_set: sys_uuid.UUID) -> "NodeSetTarget":
        return cls(node_set=node_set)

    def target_nodes(self) -> tp.List[sys_uuid.UUID]:
        return [node.uuid for node in self._fetch_nodes()]

    def owners(self) -> tp.List[sys_uuid.UUID]:
        """It's the simplest case with an ordinary node config.

        In that case, the owner and target is the node itself.
        If owners are deleted, the config will be deleted as well.
        """
        return [self.node_set]

    def _fetch_nodes(self) -> tp.List[nm.Node]:
        return nm.Node.objects.get_all(
            filters={"node_set": dm_filters.EQ(str(self.node_set))}
        )

    def are_owners_alive(self) -> bool:
        return (
            nm.NodeSet.objects.get_one_or_none(
                filters={"uuid": dm_filters.EQ(self.node_set)}
            )
            is not None
        )
