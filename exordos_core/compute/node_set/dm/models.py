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

from exordos_core.compute import constants as nc
from exordos_core.compute.dm import models as compute_models


class Node(compute_models.Node):
    @classmethod
    def get_resource_kind(cls) -> str:
        """Return the resource kind."""
        return "set_agent_node"


class Volume(compute_models.Volume):
    @classmethod
    def get_resource_kind(cls) -> str:
        """Return the resource kind."""
        return "set_agent_volume"


class NodeSet(compute_models.NodeSet):
    __derivative_model_map__ = {
        "set_agent_node": Node,
        "set_agent_volume": Volume,
    }

    def _node_network_pin(self) -> dict:
        """The network pin this set hands to each of its nodes, if it has one.

        `NodeSet.default_network` has been settable since the field existed
        and reached nothing: the nodes were generated without it, so a set
        could say where its nodes belong and be ignored. Only the pin is
        carried — anything else in there describes a port that this set
        does not have and each node gets its own.
        """
        network = (self.default_network or {}).get(nc.DEFAULT_NETWORK_KEY)
        return {nc.DEFAULT_NETWORK_KEY: str(network)} if network else {}

    def gen_nodes(
        self,
        project_id: sys_uuid.UUID,
        placement_policies: tp.Collection[compute_models.PlacementPolicy] = tuple(),
        node_uuids: tp.Collection[sys_uuid.UUID] = tuple(),
    ) -> tp.Collection[Node]:
        """Generate nodes for the node set."""
        # FIXME(akremenetsky): Perhaps this method should be moved to
        # the parent models but I'm not sure we need the logic of node
        # generation anywhere else.
        nodes = []

        # NOTE(akremenetsky): Use the simplest implementation as
        # we don't have any node set type except the default one.
        for idx in range(self.replicas):
            node_uuid = node_uuids[idx] if idx < len(node_uuids) else sys_uuid.uuid4()
            node = Node(
                uuid=node_uuid,
                node_set=self.uuid,
                name=f"{self.name}-node-{str(node_uuid)[:4]}",
                cores=self.cores,
                ram=self.ram,
                project_id=project_id,
                node_type=self.node_type,
                status=nc.NodeStatus.NEW.value,
                placement_policies=[p.uuid for p in placement_policies],
                disk_spec=self.disk_spec.node_spec(self, node_uuid),
                # What the set says about where its nodes live. Only the
                # pin travels: the rest of a node's `default_network` (its
                # port, address and MAC) is filled in when the network
                # service places it, and a set has none of that to give.
                #
                # A set that pins nothing hands over nothing, which is the
                # empty dict a node started with anyway — so an existing
                # set generates exactly the nodes it generated before, and
                # the service's own rule keeps them where they were: an
                # unpinned node never lands on an overlay subnet.
                default_network=self._node_network_pin(),
            )
            nodes.append(node)

        return nodes

    def gen_volumes(
        self,
        project_id: sys_uuid.UUID,
    ) -> tp.Collection[Volume]:
        """Create volumes for the node set."""
        # TODO(akremenetsky): The implementation is not correct since we
        # need to return right volume class. Rework this part later.
        volumes = self.disk_spec.volumes(self)
        for volume in volumes:
            volume.project_id = project_id

        return volumes
