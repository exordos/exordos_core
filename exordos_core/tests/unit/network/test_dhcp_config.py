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

"""What the host's DHCP server is handed.

One file serves every subnet of the installation, and `dhcpd` refuses the
whole of it over a single syntax error — so what one subnet renders to is
never only that subnet's business.
"""

import types

import netaddr

from exordos_core.network.dhcp import isc


class _Subnet(types.SimpleNamespace):
    """Enough of a Subnet for the renderer, without a database.

    Hashable, because the renderer is handed a mapping keyed by subnet.
    """

    __hash__ = object.__hash__


def _subnet(cidr, dns_servers=(), routers=(), next_server=None):
    return _Subnet(
        cidr=netaddr.IPNetwork(cidr),
        dns_servers=list(dns_servers),
        routers=list(routers),
        next_server=next_server,
        ip_discovery_range_pair=None,
    )


def test_a_subnet_that_names_a_resolver_hands_it_out():
    config = isc.dhcp_config({_subnet("10.20.0.0/22", ["10.20.0.2"]): []})

    assert "option domain-name-servers 10.20.0.2;" in config


def test_a_subnet_with_no_resolver_says_nothing_about_resolvers():
    """An option with no value is a syntax error, and one is enough to stop
    the server for every network on the host: a pool of addresses nothing is
    placed in names no resolver, and took PXE down installation-wide."""
    config = isc.dhcp_config({_subnet("10.20.3.0/24"): []})

    assert "domain-name-servers" not in config
    assert "subnet 10.20.3.0 netmask 255.255.255.0" in config


def test_a_pool_subnet_does_not_disturb_the_ones_beside_it():
    config = isc.dhcp_config(
        {
            _subnet("10.20.3.0/24"): [],
            _subnet("10.30.0.0/24", ["10.20.0.2"], next_server="10.30.0.2"): [],
        }
    )

    assert "option domain-name-servers 10.20.0.2;" in config
    assert "next-server 10.30.0.2;" in config
    # Nothing dangles: every option that is written has a value.
    assert "option domain-name-servers ;" not in config
