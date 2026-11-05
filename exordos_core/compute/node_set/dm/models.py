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

from gcl_sdk.infra.dm import models as sdk_models
from restalchemy.dm import filters

from exordos_core.compute import constants as nc
from exordos_core.compute.dm import models as compute_models


class Node(compute_models.Node):
    def get_resource_target_fields(self) -> tp.Collection[str]:
        fields = super().get_resource_target_fields()
        if self.default_network is not None:
            fields = fields | frozenset(("default_network",))
        return fields

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

    def _replica_disk_spec(self):
        """Default unspecified cloud replica disks to durable Cinder storage."""
        subnet_id = self.network_subnet or (self.default_network or {}).get("subnet")
        if not subnet_id and (self.default_network or {}).get("network"):
            from exordos_core.network.routed import parts

            network = compute_models.Network.objects.get_one(
                filters={"uuid": filters.EQ(self.default_network["network"])}
            )
            if network.driver_spec.get("driver") == "routed" and any(
                s.definition["driver_spec"].get("driver") == "cloud"
                for s in parts(network)
            ):
                return (
                    sdk_models.SetDisksSpec(
                        disks=[
                            {"ephemeral": False, **disk}
                            for disk in self.disk_spec.disks
                        ]
                    )
                    if isinstance(self.disk_spec, sdk_models.SetDisksSpec)
                    else self.disk_spec
                )
        if not subnet_id or not isinstance(self.disk_spec, sdk_models.SetDisksSpec):
            return self.disk_spec
        subnet = compute_models.Subnet.objects.get_one(
            filters={"uuid": filters.EQ(subnet_id)}
        )
        network = compute_models.Network.objects.get_one(
            filters={"uuid": filters.EQ(subnet.network)}
        )
        if network.driver_spec.get("driver") != "cloud":
            return self.disk_spec
        return sdk_models.SetDisksSpec(
            disks=[{"ephemeral": False, **disk} for disk in self.disk_spec.disks]
        )

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
                disk_spec=self._replica_disk_spec().node_spec(self, node_uuid),
                default_network=self.default_network,
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
        volumes = self._replica_disk_spec().volumes(self)
        for volume in volumes:
            volume.project_id = project_id

        return volumes
