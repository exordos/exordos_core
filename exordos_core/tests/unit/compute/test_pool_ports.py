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
import uuid

import pytest
from gcl_sdk.agents.universal.drivers import pool as ua_pool

from exordos_core.compute.dm import models
from exordos_core.compute.pool.dm import models as pool_models


@pytest.mark.parametrize(
    "kind", [ua_pool.LibvirtPoolDriverSpec, ua_pool.ExordosLocalHyperDriverSpec]
)
def test_pool_default_ovs_is_explicit(kind):
    kwargs = (
        {"node": uuid.uuid4()} if kind is ua_pool.ExordosLocalHyperDriverSpec else {}
    )
    spec = kind(connection_uri="qemu:///system", **kwargs)
    pool = models.MachinePool(name="pool", driver_spec=spec)
    assert pool.dump_to_simple_view()["driver_spec"]["ovs"] is False


def test_machine_target_does_not_contain_port_metadata():
    machine = mock.MagicMock(
        uuid=uuid.uuid4(),
        project_id=uuid.uuid4(),
        cores=1,
        ram=512,
        image=None,
        machine_type="VM",
        boot="hd0",
    )
    machine.name = "guest"
    machine.node.uuid, machine.pool.uuid = uuid.uuid4(), uuid.uuid4()
    target = pool_models.PoolMachine.from_machine(machine).to_ua_resource()
    assert "port_info" not in target.value
    assert "ports_info" not in target.value
    assert target.value["uuid"] == str(machine.uuid)


def test_port_target_has_its_own_uuid_and_attachment_contract():
    port = pool_models.PoolPort(
        uuid=uuid.uuid4(),
        project_id=uuid.uuid4(),
        pool=uuid.uuid4(),
        machine=uuid.uuid4(),
        mac="52:54:00:00:00:01",
        source="tenant",
        overlay=True,
    )
    target = port.to_ua_resource()
    assert target.kind == "pool_port"
    assert target.uuid == port.uuid
    assert target.value["machine"] == str(port.machine)
    assert target.value["attached"] is True
    actual = ua_pool.MetaPort.from_ua_resource(target)
    assert actual.to_ua_resource("pool_port").hash == target.hash
