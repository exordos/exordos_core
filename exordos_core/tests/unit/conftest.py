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

import uuid as sys_uuid

import netaddr
import pytest

from exordos_core.common import constants as c
from exordos_core.compute.dm import models
from exordos_core.network import ipam


@pytest.fixture
def empty_ipam() -> ipam.Ipam:
    subnet = models.Subnet(
        network=sys_uuid.uuid4(),
        cidr=netaddr.IPNetwork("0.0.0.0/24"),
        project_id=c.ZERO_UUID,
    )
    return ipam.Ipam({subnet: []})
