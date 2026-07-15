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

from exordos_core.elements import constants as cc
from exordos_core.network.border.builders import paas as border_paas
from exordos_core.network.border.dm import models as border_models


def test_paasborder_getters_read_inline_rules():
    snat = [{"source_cidr": "192.168.100.0/24", "mode": "masquerade", "snat_to": None}]
    forwards = [
        {
            "proto": "tcp",
            "public_ip": None,
            "listen_port": 443,
            "to_host": "192.168.100.2",
            "to_port": 443,
        }
    ]
    border = border_models.PaasBorder(
        uuid=sys_uuid.uuid4(),
        name="b",
        project_id=sys_uuid.uuid4(),
        snat_rules=snat,
        forwards=forwards,
    )
    assert border.get_snat_rules() == snat
    assert border.get_forwards() == forwards
    assert border.get_routes() == []


def _instance(snat=None, forwards=None, routes=None, node=None, kind="core_agent"):
    inst = mock.MagicMock()
    inst.uuid = sys_uuid.uuid4()
    inst.node = node
    inst.type.kind = kind
    inst.get_snat_rules.return_value = snat or []
    inst.get_forwards.return_value = forwards or []
    inst.get_routes.return_value = routes or []
    return inst


def _collection(*statuses):
    objs = tuple(mock.MagicMock(actual=mock.MagicMock(status=s)) for s in statuses)
    return paas_builder.PaaSCollection(paas_objects=objs)


def test_produces_single_border_agent_passing_rules_through():
    snat = [
        {
            "uuid": "r1",
            "source_cidr": "192.168.100.0/24",
            "mode": "masquerade",
            "snat_to": None,
        }
    ]
    forwards = [
        {
            "uuid": "f1",
            "proto": "tcp",
            "public_ip": None,
            "listen_port": 22001,
            "to_host": "192.168.100.2",
            "to_port": 22,
        }
    ]
    inst = _instance(snat=snat, forwards=forwards)

    resources = border_paas.BorderBuilder().create_paas_objects(inst)

    assert len(resources) == 1
    agent = resources[0]
    # No target node -> scheduled by capability to the core node's agent.
    assert agent.get_resource_kind() == "border_agent"
    assert agent.uuid == inst.uuid
    assert agent.snat_rules == snat
    assert agent.forwards == forwards
    assert agent.routes == []


def test_produces_border_node_when_target_node_set():
    node = sys_uuid.uuid4()
    forwards = [
        {
            "uuid": "f1",
            "proto": "tcp",
            "public_ip": None,
            "listen_port": 22001,
            "to_host": "192.168.100.2",
            "to_port": 22,
        }
    ]
    inst = _instance(forwards=forwards, node=node)

    resources = border_paas.BorderBuilder().create_paas_objects(inst)

    assert len(resources) == 1
    node_res = resources[0]
    # Targeted -> border_node scheduled to that node's agent.
    assert node_res.get_resource_kind() == "border_node"
    assert node_res.uuid == inst.uuid
    assert node_res.agent_uuid == node
    assert node_res.forwards == forwards


def test_core_kind_schedules_border_node_per_vm_node():
    vm_node = sys_uuid.uuid4()
    snat = [{"source_cidr": "10.77.0.0/24", "mode": "masquerade", "snat_to": None}]
    inst = _instance(snat=snat, kind="core")

    b = border_paas.BorderBuilder()
    with mock.patch.object(b, "_get_iaas_nodes", return_value=[str(vm_node)]):
        resources = b.create_paas_objects(inst)

    assert len(resources) == 1
    node_res = resources[0]
    assert node_res.get_resource_kind() == "border_node"
    # The capability lands on the dedicated VM's agent.
    assert node_res.uuid == vm_node
    assert node_res.agent_uuid == vm_node
    assert node_res.snat_rules == snat


def test_core_kind_defers_until_the_vm_exists():
    inst = _instance(kind="core")

    b = border_paas.BorderBuilder()
    with mock.patch.object(b, "_get_iaas_nodes", return_value=[]):
        resources = b.create_paas_objects(inst)

    assert resources == []
    assert inst.status == cc.ServiceStatus.IN_PROGRESS.value


def test_pinned_node_wins_over_core_kind():
    node = sys_uuid.uuid4()
    inst = _instance(node=node, kind="core")

    resources = border_paas.BorderBuilder().create_paas_objects(inst)

    assert len(resources) == 1
    assert resources[0].get_resource_kind() == "border_node"
    assert resources[0].agent_uuid == node


def test_iaas_builder_provisions_single_vm_for_core_kind():
    from oslo_config import cfg

    from exordos_core.network.border.builders import iaas as border_iaas

    try:
        cfg.CONF.register_opts(
            [cfg.StrOpt("border_image", default="http://img/border.raw.zst")],
            "gservice",
        )
    except cfg.DuplicateOptError:
        pass

    inst = _instance(kind="core")
    inst.name = "gw"
    inst.type.cpu = 2
    inst.type.ram = 1024
    inst.type.disk_size = 12

    b = border_iaas.BorderIaasBuilder.__new__(border_iaas.BorderIaasBuilder)
    b._project_id = sys_uuid.uuid4()

    objs = b.create_infra(inst)
    assert len(objs) == 1
    ns = objs[0]
    assert ns.get_resource_kind() == "target_node_set"
    assert ns.replicas == 1
    assert ns.cores == 2 and ns.ram == 1024
    assert ns.disk_spec.image.endswith("border.raw.zst")

    # core_agent and pinned borders never provision a VM
    assert b.create_infra(_instance(kind="core_agent")) == []
    assert b.create_infra(_instance(kind="core", node=sys_uuid.uuid4())) == []


def test_status_in_progress_when_no_actual_objects_yet():
    inst = _instance()
    border_paas.BorderBuilder().actualize_paas_objects(inst, _collection())
    assert inst.status == cc.ServiceStatus.IN_PROGRESS.value


def test_status_active_when_all_actual_active():
    inst = _instance()
    border_paas.BorderBuilder().actualize_paas_objects(
        inst, _collection("ACTIVE", "ACTIVE")
    )
    assert inst.status == cc.ServiceStatus.ACTIVE.value


def test_status_error_when_any_actual_error():
    inst = _instance()
    border_paas.BorderBuilder().actualize_paas_objects(
        inst, _collection("ACTIVE", "ERROR")
    )
    assert inst.status == cc.ServiceStatus.ERROR.value
