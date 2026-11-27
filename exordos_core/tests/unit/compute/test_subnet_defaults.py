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

"""Subnet defaults are calculated without changing the stored intent."""

from unittest import mock
import uuid as sys_uuid

import netaddr
import pytest

from exordos_core.common import exceptions
from exordos_core.compute.dm import models
from exordos_core.network import ipam
from exordos_core.network import subnet as subnet_utils
from exordos_core.user_api.network.dm import models as catalog


def _subnet(**overrides):
    view = {
        "uuid": str(sys_uuid.uuid4()),
        "name": "s",
        "network": str(sys_uuid.uuid4()),
        "project_id": str(sys_uuid.uuid4()),
        "cidr": "10.101.0.0/24",
        "dhcp": True,
        "routers": None,
        "dns_servers": None,
        "ip_range": None,
        "ip_discovery_range": None,
        "next_server": None,
    }
    view.update(overrides)
    return models.Subnet.restore_from_simple_view(**view)


@pytest.mark.parametrize("dhcp", [True, False])
@pytest.mark.parametrize(
    "routers,dns_servers",
    [
        (None, None),
        ([], []),
        ([{"to": "10.0.0.0/8", "via": "10.101.0.9"}], ["8.8.8.8"]),
        (None, ["8.8.8.8"]),
        ([{"to": "0.0.0.0/0", "via": "10.101.0.9"}], None),
    ],
)
def test_subnet_defaults_preserve_raw_intent(dhcp, routers, dns_servers):
    subnet = _subnet(dhcp=dhcp, routers=routers, dns_servers=dns_servers)
    expected_routes = (
        routers
        if routers is not None
        else ([{"to": "0.0.0.0/0", "via": "10.101.0.1"}] if dhcp else [])
    )
    expected_dns = (
        dns_servers if dns_servers is not None else (["10.101.0.1"] if dhcp else [])
    )
    assert [
        {key: str(value) for key, value in route.items()}
        for route in subnet_utils.effective_routers(subnet)
    ] == expected_routes
    assert subnet_utils.effective_dns_servers(subnet) == expected_dns
    view = subnet.dump_to_simple_view()
    assert view["routers"] == routers
    assert view["dns_servers"] == dns_servers
    assert not {"effective_routers", "effective_dns_servers"} & view.keys()


@pytest.mark.parametrize("cidr", ["10.9.9.0/31", "10.9.9.1/32"])
def test_tiny_subnet_has_no_room_for_a_gateway(cidr):
    subnet = _subnet(cidr=cidr)
    assert subnet_utils.effective_routers(subnet) == []
    assert subnet_utils.effective_dns_servers(subnet) == []


@pytest.mark.parametrize("dhcp", [True, False])
@pytest.mark.parametrize(
    "routers", [None, [], [{"to": "0.0.0.0/0", "via": "10.101.0.9"}]]
)
def test_ipam_and_address_ledger_reserve_the_calculated_gateway(dhcp, routers):
    subnet = _subnet(dhcp=dhcp, routers=routers)
    gateway = (
        "10.101.0.9"
        if routers
        else ("10.101.0.1" if routers is None and dhcp else None)
    )
    assert ipam.Ipam.infrastructure_addresses(subnet) == (
        [netaddr.IPAddress(gateway)] if gateway else []
    )
    address = catalog.Address(
        project_id=subnet.project_id, kind="reservation", subnet=subnet.uuid
    )
    assert address._reserved_addresses(subnet) == {
        "10.101.0.0",
        "10.101.0.255",
        *([gateway] if gateway else []),
    }
    assert str(ipam.Ipam({subnet: []}).allocate_ip(subnet)) == (
        "10.101.0.2" if gateway == "10.101.0.1" else "10.101.0.1"
    )


@pytest.mark.parametrize("routers", [[], [{"to": "0.0.0.0/0", "via": "2001:db8::1"}]])
def test_overlay_subnet_rejects_ipv6_at_model_boundary(routers):
    subnet = _subnet(cidr="2001:db8::/64", routers=routers)
    network = mock.Mock(driver_spec={"driver": "ovs_evpn"})
    with mock.patch.object(models.Network, "objects") as objects:
        objects.get_one_or_none.return_value = network
        with pytest.raises(exceptions.ValidateException, match="IPv4"):
            subnet._validate_routes()


def test_overlay_subnet_rejects_ipv6_route_destinations():
    subnet = _subnet(routers=[{"to": "2001:db8::/64", "via": "10.101.0.1"}])
    with mock.patch.object(models.Network, "objects") as objects:
        objects.get_one_or_none.return_value = mock.Mock(
            driver_spec={"driver": "ovs_evpn"}
        )
        with pytest.raises(exceptions.ValidateException, match="IPv4 routes"):
            subnet._validate_routes()
