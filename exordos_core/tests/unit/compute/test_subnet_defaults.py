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

"""Default gateway route and resolver materialized at subnet creation.

The materialization itself lives in ``Subnet.insert`` (create only); these
tests exercise its worker ``_default_gateway_services`` on an in-memory model
so they need no database.
"""

import uuid as sys_uuid

from exordos_core.compute.dm import models


def _subnet(**overrides):
    view = {
        "uuid": str(sys_uuid.uuid4()),
        "name": "s",
        "network": str(sys_uuid.uuid4()),
        "project_id": str(sys_uuid.uuid4()),
        "cidr": "10.101.0.0/24",
        "dhcp": True,
        "routers": [],
        "dns_servers": [],
        "ip_range": None,
        "ip_discovery_range": None,
        "next_server": None,
    }
    view.update(overrides)
    subnet = models.Subnet.restore_from_simple_view(**view)
    subnet._default_gateway_services()
    return subnet.dump_to_simple_view()


def test_dhcp_subnet_defaults_gateway_and_dns_to_first_host():
    view = _subnet()
    assert view["routers"] == [{"to": "0.0.0.0/0", "via": "10.101.0.1"}]
    assert view["dns_servers"] == ["10.101.0.1"]


def test_dhcpless_subnet_gets_nothing():
    """A floating-IP pool serves no guests — no gateway to reserve."""
    view = _subnet(dhcp=False)
    assert view["routers"] == []
    assert view["dns_servers"] == []


def test_explicit_routers_are_left_alone_but_dns_still_defaults():
    view = _subnet(routers=[{"to": "10.0.0.0/8", "via": "10.101.0.9"}])
    assert view["routers"] == [{"to": "10.0.0.0/8", "via": "10.101.0.9"}]
    assert view["dns_servers"] == ["10.101.0.1"]


def test_explicit_dns_is_left_alone_but_gateway_still_defaults():
    view = _subnet(dns_servers=["8.8.8.8"])
    assert view["routers"] == [{"to": "0.0.0.0/0", "via": "10.101.0.1"}]
    assert view["dns_servers"] == ["8.8.8.8"]


def test_tiny_subnet_has_no_room_for_a_gateway():
    """A /31 is only network/broadcast — nothing to hand out."""
    view = _subnet(cidr="10.9.9.0/31")
    assert view["routers"] == []
    assert view["dns_servers"] == []
