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

import netaddr

from exordos_core.compute.dm import models as compute_models
from exordos_core.network import service
from exordos_core.network.dm import models as net_models


def test_a_bare_metal_node_with_two_interfaces_gets_two_device_slots():
    """Each addressed interface of a bare-metal node is a row of its own.

    Two ports both at index 0 break the (node, index) uniqueness: the second
    insert fails and that interface is never given a port.
    """
    project = sys_uuid.uuid4()
    node = mock.MagicMock(spec=compute_models.Node, uuid=sys_uuid.uuid4())
    subnet = net_models.Subnet(
        name="flat",
        project_id=project,
        cidr=netaddr.IPNetwork("10.20.0.0/22"),
    )
    rows = [
        mock.MagicMock(
            node=node,
            machine=mock.MagicMock(spec=compute_models.Machine, uuid=sys_uuid.uuid4()),
            iface=mock.MagicMock(ipv4=netaddr.IPAddress(ip), mac=mac),
        )
        for ip, mac in (
            ("10.20.0.10", "52:54:00:00:00:01"),
            ("10.20.0.11", "52:54:00:00:00:02"),
        )
    ]

    with mock.patch.object(
        net_models.HWNodeWithoutPorts, "get_nodes", return_value=rows
    ):
        ports = service.NetworkService._get_new_hw_ports(mock.MagicMock(), [subnet])

    assert sorted(p.index for p in ports) == [0, 1]
