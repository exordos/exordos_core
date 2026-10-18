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

import netaddr
import pytest

from exordos_core.network import ipam


class TestIPAM:
    def test_full_pool(self, empty_ipam: ipam.Ipam):
        # The network and broadcast addresses are not hosts, so a subnet
        # without an explicit range pools everything between them.
        _, pool = empty_ipam._pool_map.popitem()
        assert pool == [(1, 254)]

    def test_occupy_at_start(self, empty_ipam: ipam.Ipam):
        address_pool = [(0, 10)]
        empty_ipam.occupy_ip(0, address_pool)
        assert address_pool == [(1, 10)]

    def test_occupy_at_end(self, empty_ipam: ipam.Ipam):
        address_pool = [(0, 10)]
        empty_ipam.occupy_ip(10, address_pool)
        assert address_pool == [(0, 9)]

    def test_occupy_in_middle(self, empty_ipam: ipam.Ipam):
        address_pool = [(0, 10)]
        empty_ipam.occupy_ip(5, address_pool)
        assert address_pool == [(0, 4), (6, 10)]

    def test_occupy_last(self, empty_ipam: ipam.Ipam):
        address_pool = [(0, 0)]
        empty_ipam.occupy_ip(0, address_pool)
        assert address_pool == []

    # Disable because of:
    # https://github.com/infraguys/exordos_core/pull/52
    # def test_occupy_not_in_pool(self, empty_ipam: ipam.Ipam):
    #     address_pool = [(0, 10)]
    #     with pytest.raises(ipam.IpamIpNotFound) as e:
    #         empty_ipam.occupy_ip(20, address_pool)
    #         assert e.ip == netaddr.IPAddress("0.0.0.20")

    def test_allocate_ip(self, empty_ipam: ipam.Ipam):
        subnet = list(empty_ipam._pool_map.keys())[0]
        assert empty_ipam.allocate_ip(subnet) == netaddr.IPAddress("0.0.0.1")

    def test_allocate_ip_target(self, empty_ipam: ipam.Ipam):
        subnet = list(empty_ipam._pool_map.keys())[0]
        assert empty_ipam.allocate_ip(
            subnet, netaddr.IPAddress("0.0.0.10")
        ) == netaddr.IPAddress("0.0.0.10")
        assert empty_ipam._pool_map[subnet] == [(1, 9), (11, 254)]

    def test_allocate_ip_no_available_ips(self, empty_ipam: ipam.Ipam):
        subnet = list(empty_ipam._pool_map.keys())[0]
        empty_ipam._pool_map[subnet] = []
        with pytest.raises(ipam.IpamNoIPsAvailable):
            empty_ipam.allocate_ip(subnet)

    def test_deallocate_to_start(self, empty_ipam: ipam.Ipam):
        subnet = list(empty_ipam._pool_map.keys())[0]
        empty_ipam.occupy_ip(1, empty_ipam._pool_map[subnet])

        assert empty_ipam._pool_map[subnet] == [(2, 254)]

        empty_ipam.deallocate_ip(subnet, netaddr.IPAddress("0.0.0.1"))
        assert empty_ipam._pool_map[subnet] == [(1, 254)]

    def test_deallocate_to_end(self, empty_ipam: ipam.Ipam):
        subnet = list(empty_ipam._pool_map.keys())[0]
        empty_ipam.occupy_ip(254, empty_ipam._pool_map[subnet])

        assert empty_ipam._pool_map[subnet] == [(1, 253)]

        empty_ipam.deallocate_ip(subnet, netaddr.IPAddress("0.0.0.254"))
        assert empty_ipam._pool_map[subnet] == [(1, 254)]

    def test_deallocate_in_middle(self, empty_ipam: ipam.Ipam):
        subnet = list(empty_ipam._pool_map.keys())[0]
        empty_ipam.occupy_ip(128, empty_ipam._pool_map[subnet])

        assert empty_ipam._pool_map[subnet] == [(1, 127), (129, 254)]

        empty_ipam.deallocate_ip(subnet, netaddr.IPAddress("0.0.0.128"))
        assert empty_ipam._pool_map[subnet] == [(1, 254)]

    def test_deallocate_already_deallocated(self, empty_ipam: ipam.Ipam):
        subnet = list(empty_ipam._pool_map.keys())[0]
        assert empty_ipam._pool_map[subnet] == [(1, 254)]

        empty_ipam.deallocate_ip(subnet, netaddr.IPAddress("0.0.0.128"))
        assert empty_ipam._pool_map[subnet] == [(1, 254)]

    def test_deallocate_not_in_pool(self, empty_ipam: ipam.Ipam):
        subnet = list(empty_ipam._pool_map.keys())[0]
        assert empty_ipam._pool_map[subnet] == [(1, 254)]

        empty_ipam.deallocate_ip(subnet, netaddr.IPAddress("0.0.1.1"))
        assert empty_ipam._pool_map[subnet] == [(1, 254), (257, 257)]
