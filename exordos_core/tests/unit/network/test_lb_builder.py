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

from unittest import mock
import uuid as sys_uuid

from gcl_sdk.paas.services import builder as paas_builder

from exordos_core.network.lb.builders import iaas as lb_iaas
from exordos_core.network.lb.builders import paas as lb_paas
from exordos_core.network.lb.dm import models as lb_models


def _node_lb(node):
    inst = mock.MagicMock()
    inst.uuid = sys_uuid.uuid4()
    inst.type.kind = "node"
    inst.type.node = node
    inst.get_vhosts.return_value = [{"uuid": "v1"}]
    inst.get_backend_pools.return_value = {"p1": {"endpoints": []}}
    return inst


def test_node_lb_is_pinned_to_the_node_agent():
    node = sys_uuid.uuid4()
    inst = _node_lb(node)

    res = lb_paas.LBBuilder().actualize_paas_objects(
        inst, paas_builder.PaaSCollection(paas_objects=tuple())
    )

    assert len(res) == 1
    (obj,) = res
    assert isinstance(obj, lb_models.PaasLBNode)
    # The LB uuid, so that several LBs can share one node.
    assert obj.uuid == inst.uuid
    assert obj.agent_uuid == node
    assert obj.vhosts == [{"uuid": "v1"}]
    assert obj.backend_pools == {"p1": {"endpoints": []}}


def test_node_lb_needs_no_infra():
    builder = lb_iaas.LBBuilder(lb_models.IaasLB, project_id=sys_uuid.uuid4())

    assert builder.create_infra(_node_lb(sys_uuid.uuid4())) == []
