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
from gcl_sdk.infra import constants as sdk_c
from gcl_sdk.infra.dm import models as sdk_models
from gcl_sdk.infra.services import builder
from oslo_config import cfg

from exordos_core.network.border.dm import models
from exordos_core.network.lb.dm import models as lb_models

LOG = logging.getLogger(__name__)
CONF = cfg.CONF

NODE_SET_KIND = lb_models.TargetNodeSet.get_resource_kind()


class BorderIaasBuilder(builder.CoreInfraBuilder):
    """Provisions the dedicated VM of a `core` (VM-based) border.

    Calque of the LB iaas builder: one single-replica target_node_set from
    ``[gservice] border_image`` (an image whose universal agent ships
    BorderCapabilityDriver). The paas builder schedules the ``border_node``
    capability onto that VM's agent, same as it does for a pinned node.
    """

    _name_prefix = "border"

    def __init__(
        self,
        instance_model: tp.Type[models.IaasBorder],
        project_id: sys_uuid.UUID,
    ):
        super().__init__(instance_model)
        self._project_id = project_id

    def create_infra(
        self, instance: models.IaasBorder
    ) -> tp.Collection[ua_models.TargetResourceKindAwareMixin]:
        # A pinned border (node set) never provisions its own VM,
        # whatever the type says.
        if instance.node or instance.type.kind != "core":
            return []

        node_set = lb_models.TargetNodeSet(
            uuid=instance.uuid,
            name=f"{self._name_prefix}-{instance.name}",
            cores=instance.type.cpu,
            ram=instance.type.ram,
            replicas=1,
            project_id=self._project_id,
            status=sdk_c.NodeStatus.NEW.value,
            disk_spec=sdk_models.SetRootDiskSpec(
                size=instance.type.disk_size,
                image=CONF.gservice.border_image,
            ),
        )
        return [node_set]

    def actualize_infra(
        self,
        instance: models.IaasBorder,
        infra: builder.InfraCollection,
    ) -> tp.Collection[ua_models.TargetResourceKindAwareMixin]:
        if instance.node or instance.type.kind != "core":
            return tuple()

        nodeset = None
        tgt_nodeset = None
        for target, actual in infra.infra_objects:
            if target.get_resource_kind() == NODE_SET_KIND:
                nodeset = actual
                target.cores = instance.type.cpu
                target.ram = instance.type.ram
                target.replicas = 1
                tgt_nodeset = target
                break
        else:
            raise ValueError("A core border must have its target_node_set!")

        if nodeset and nodeset.nodes:
            instance.ipsv4 = [node["ipv4"] for node in nodeset.nodes.values()]

        # The paas builder owns the final ACTIVE/ERROR flip (it tracks the
        # border_node capability); here only reflect VM provisioning.
        try:
            if sdk_c.InstanceStatus(nodeset.status) == sdk_c.InstanceStatus.ERROR:
                instance.status = sdk_c.InstanceStatus.ERROR.value
        except (ValueError, AttributeError):
            pass

        return (tgt_nodeset,)
