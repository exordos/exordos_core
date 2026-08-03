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

import uuid as sys_uuid

import netaddr

from exordos_core.network import ipam as net_ipam


class _Subnet:
    """The fields the allocator reads off a subnet."""

    def __init__(
        self,
        cidr="10.101.0.0/24",
        ip_range=None,
        routers=(),
        next_server=None,
    ):
        self.uuid = sys_uuid.uuid4()
        self.cidr = netaddr.IPNetwork(cidr)
        self.ip_range = ip_range
        self.ip_discovery_range = None
        self.routers = list(routers)
        self.next_server = next_server

    @property
    def ip_range_pair(self):
        if self.ip_range is None:
            return None
        first, last = self.ip_range.split("-")
        return netaddr.IPAddress(first), netaddr.IPAddress(last)

    def __hash__(self):
        return hash(self.uuid)


def _allocate(subnet, count):
    ipam = net_ipam.Ipam({subnet: []})
    return [str(ipam.allocate_ip(subnet)) for _ in range(count)]


def test_a_subnet_without_a_range_skips_its_network_address():
    """The first guest of a fresh subnet used to be handed 10.101.0.0."""
    assert _allocate(_Subnet(), 2) == ["10.101.0.1", "10.101.0.2"]


def test_the_gateway_is_never_handed_to_a_guest():
    subnet = _Subnet(routers=[{"to": "0.0.0.0/0", "via": "10.101.0.1"}])
    assert _allocate(subnet, 2) == ["10.101.0.2", "10.101.0.3"]


def test_the_netboot_server_is_never_handed_to_a_guest():
    subnet = _Subnet(next_server="10.101.0.2")
    assert _allocate(subnet, 3) == ["10.101.0.1", "10.101.0.3", "10.101.0.4"]


def test_the_broadcast_address_is_not_in_the_pool():
    subnet = _Subnet(cidr="10.101.0.0/30")
    assert _allocate(subnet, 2) == ["10.101.0.1", "10.101.0.2"]


def test_an_explicit_range_still_wins():
    subnet = _Subnet(ip_range="10.101.0.20-10.101.0.21")
    assert _allocate(subnet, 2) == ["10.101.0.20", "10.101.0.21"]


def test_a_gateway_outside_the_subnet_reserves_nothing():
    subnet = _Subnet(routers=[{"to": "0.0.0.0/0", "via": "10.200.0.1"}])
    assert _allocate(subnet, 1) == ["10.101.0.1"]
