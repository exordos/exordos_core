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

"""Managed OST virtual machines, created and retired by core reconciliation."""

import logging
import re
import uuid

from gcl_sdk.agents.universal.dm import models as ua
from gcl_sdk.agents.universal.drivers import rawstor_specs
from gcl_sdk.infra.dm import models as infra
from restalchemy.dm import filters

from exordos_core.common import constants as cc
from exordos_core.compute.dm import models as compute
from exordos_core.storage.dm import models
from exordos_core.vs.dm import models as vs

LOG = logging.getLogger(__name__)
IMAGE_VARIABLE = uuid.UUID("9b7c0dfa-1562-4e87-88d5-e3af8cb10725")
LOCATION = "zfs://rawstor-ost/data"


def vm_uuid(cluster, index):
    return uuid.uuid5(cluster.uuid, f"ost-vm-{index}")


def disk_spec(image, size):
    return infra.DisksSpec(
        disks=[
            {
                "size": 10,
                "image": image,
                "label": "root",
                "tier": "WARM",
                "ephemeral": True,
            },
            {"size": size, "label": "data", "tier": "WARM", "ephemeral": True},
        ]
    )


def reconcile(cluster):
    if not cluster.nodes:
        return
    variable = vs.Variable.objects.get_one_or_none(
        filters={"uuid": filters.EQ(IMAGE_VARIABLE)}
    )
    if variable is None or not variable.value:
        LOG.warning("OST image has not been published for cluster %s", cluster.uuid)
        return
    for index in range(cluster.nodes):
        node_id = vm_uuid(cluster, index)
        node = compute.Node.objects.get_one_or_none(
            filters={"uuid": filters.EQ(node_id)}
        )
        if node is None:
            prefix = re.sub(r"[^A-Za-z0-9_.-]", "-", cluster.name)[:200]
            node = compute.Node(
                uuid=node_id,
                project_id=cc.EM_PROJECT_ID,
                name=f"{prefix}-ost-{index + 1}",
                hostname=f"rawstor-ost-{node_id.hex[:12]}",
                cores=2,
                ram=2048,
                disk_spec=disk_spec(variable.value, cluster.size),
            )
            node.insert()
        address = node.default_network.get("ipv4")
        if not address:
            continue
        agents = [
            a
            for a in ua.UniversalAgent.objects.get_all(
                filters={"node": filters.EQ(node_id)}
            )
            if "storage_node" in a.list_capabilities and a.status == "ACTIVE"
        ]
        if len(agents) != 1:
            continue
        ost_id = uuid.uuid5(cluster.uuid, f"ost-{index}")
        if (
            models.StorageNode.objects.get_one_or_none(
                filters={"uuid": filters.EQ(ost_id)}
            )
            is not None
        ):
            continue
        host = f"[{address}]" if ":" in address else address
        models.StorageNode(
            uuid=ost_id,
            cluster=cluster.uuid,
            agent=agents[0].uuid,
            name=node.name,
            driver_spec=rawstor_specs.RawstorNodeDriverSpec(
                endpoint=f"ost://{host}:7777",
                bind_address=f"{host}:7777",
                location=LOCATION,
                failure_domain_path=str(node_id),
                weight=100,
            ),
        ).insert()


def delete_nodes(cluster):
    # Called only once all OST target and actual resources have disappeared.
    for index in range(cluster.nodes):
        node = compute.Node.objects.get_one_or_none(
            filters={"uuid": filters.EQ(vm_uuid(cluster, index))}
        )
        if node is not None:
            node.delete()
