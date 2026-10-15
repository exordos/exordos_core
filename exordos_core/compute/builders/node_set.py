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

from gcl_sdk.infra.services import builder
from restalchemy.dm import filters as dm_filters

from exordos_core.compute import constants as nc
from exordos_core.compute.dm import models as compute_models
from exordos_core.compute.node_set.dm import models
from exordos_core.user_api.network.dm import models as network_models

LOG = logging.getLogger(__name__)


class NodeSetBuilderService(builder.CoreInfraBuilder):
    def __init__(
        self,
        instance_model: tp.Type[models.NodeSet],
        project_id: sys_uuid.UUID,
    ):
        super().__init__(instance_model)
        self._project_id = project_id

    def _get_or_create_placement_policy(
        self,
        instance: models.NodeSet,
    ) -> compute_models.PlacementPolicy:
        # TODO(akremenetsky): Rework default placement policy creation
        # NOTE(akremenetsky): Default placement policy is soft-anti-affinity
        policy_uuid = sys_uuid.uuid5(instance.uuid, "soft-anti-affinity")

        soft_anti_affinity = compute_models.PlacementPolicy.objects.get_one_or_none(
            filters={
                "uuid": dm_filters.EQ(policy_uuid),
            },
        )

        if not soft_anti_affinity:
            soft_anti_affinity = compute_models.PlacementPolicy(
                uuid=policy_uuid,
                name="soft-anti-affinity",
                description=(
                    f"Soft anti-affinity placement policy for node set {instance.uuid}"
                ),
                kind=nc.PlacementPolicyKind.SOFT_ANTI_AFFINITY.value,
                domain=None,
                zone=None,
                project_id=self._project_id,
            )
            soft_anti_affinity.save()

        return soft_anti_affinity

    def create_infra(
        self, instance: models.NodeSet
    ) -> tp.Collection[models.Node | models.Volume]:
        """Create a list of infrastructure objects.

        The method returns a list of infrastructure objects that are required
        for the instance. For example, nodes, sets, configs, etc.
        """
        soft_anti_affinity = self._get_or_create_placement_policy(instance)
        nodes = instance.gen_nodes(
            self._project_id, placement_policies=[soft_anti_affinity]
        )
        volumes = instance.gen_volumes(self._project_id)

        return tuple(nodes) + tuple(volumes)

    def actualize_infra(
        self,
        instance: models.NodeSet,
        infra: builder.InfraCollection,
    ) -> tp.Collection[models.Node | models.Volume]:
        """Actualize the infrastructure objects.

        The method is called when the instance is outdated. For example,
        the instance `Config` has derivative `Render`. Single `Config` may
        have multiple `Render` derivatives. If any of the derivatives is
        outdated, this method is called to reactualize this infrastructure.

        Args:
            instance: The instance to actualize.
            infra: The infrastructure objects.
        """
        actual_nodes = {}
        nodes_status = []
        status = instance.status

        soft_anti_affinity = self._get_or_create_placement_policy(instance)

        node_uuids = []
        for target, _ in infra.infra_objects:
            if isinstance(target, models.Node):
                node_uuids.append(target.uuid)

        # Get the target nodes for the node set based on the current
        # configuration. This will be used to filter out nodes that are
        # being deleted during a shrink operation.
        nodes = instance.gen_nodes(
            self._project_id,
            placement_policies=[soft_anti_affinity],
            node_uuids=node_uuids,
        )
        volumes = instance.gen_volumes(self._project_id)

        new_target_nodes = {n.uuid: n for n in nodes}

        primary_ports = [
            actual.default_network["port"]
            for _, actual in infra.infra_objects
            if isinstance(actual, models.Node)
            and actual.uuid in new_target_nodes
            and (actual.default_network or {}).get("port")
        ]
        floating_ips = {
            str(address.port): str(address.address)
            for address in (
                network_models.Address.objects.get_all(
                    filters={
                        "port": dm_filters.In(primary_ports),
                        "project_id": dm_filters.EQ(instance.project_id),
                        "kind": dm_filters.EQ("floating"),
                        "delete_requested": dm_filters.EQ(False),
                    }
                )
                if primary_ports
                else []
            )
        }

        for _, actual in infra.infra_objects:
            if actual is None:
                continue

            # Volume part
            # FIXME(akremenetsky): So far all volumes are handled
            # at node level, skip here.
            if isinstance(actual, models.Volume):
                continue

            # Node part

            # Skip the nodes that are not in the target nodes.
            # They will be deleted.
            if actual.uuid not in new_target_nodes:
                continue

            uuid_str = str(actual.uuid)
            actual_nodes[uuid_str] = {}

            if ipv4 := (actual.default_network or {}).get("ipv4"):
                actual_nodes[uuid_str]["ipv4"] = ipv4
            if floating_ip := floating_ips.get(
                str((actual.default_network or {}).get("port"))
            ):
                actual_nodes[uuid_str]["floating_ip"] = floating_ip

            nodes_status.append(actual.status)

        instance.nodes = actual_nodes

        # Set the status to active if all nodes are active.
        # It's normal to compare the length of nodes_status with replicas
        # for the default node set type.
        if len(nodes_status) >= instance.replicas and all(
            s == nc.NodeStatus.ACTIVE for s in nodes_status
        ):
            status = nc.NodeStatus.ACTIVE.value
        elif any(s == nc.NodeStatus.ERROR for s in nodes_status):
            status = nc.NodeStatus.ERROR.value
        elif any(s == nc.NodeStatus.NEW for s in nodes_status):
            status = nc.NodeStatus.NEW.value
        elif any(s == nc.NodeStatus.IN_PROGRESS for s in nodes_status):
            status = nc.NodeStatus.IN_PROGRESS.value

        if status != instance.status:
            instance.status = status

        return tuple(new_target_nodes.values()) + tuple(volumes)
