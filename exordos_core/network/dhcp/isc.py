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

import typing as tp

import netaddr

from exordos_core.compute.dm import models


class StaticRoute(tp.NamedTuple):
    to: netaddr.IPNetwork
    via: netaddr.IPAddress

    @property
    def is_default(self) -> bool:
        return self.to == netaddr.IPNetwork("0.0.0.0/0")

    def to_rfc3442(self) -> str:
        gw = str(self.via).replace(".", ",")

        if self.is_default:
            return f" 0, {gw},"

        # Special mask format for rfc3442
        # 10.130.4.0 -> 10,130,4,
        mask = []
        for d in reversed(str(self.to.ip).split(".")):
            if not mask and d == "0":
                continue
            mask.append(d)

        mask = ",".join(reversed(mask))
        return f" {self.to.prefixlen}, {mask}, {gw},"


DHCP_ISC_SVC_NAME = "isc-dhcp-server.service"


_common_settings = """# Common settings
allow bootp;
allow booting;
option ip-forwarding false;
option mask-supplier false;
max-lease-time 1209600;
default-lease-time 1209600;
log-facility local7;

option rfc3442-classless-static-routes code 121 = array of integer 8;
"""


_netboot_template = """

	# Netboot
	if substring (option vendor-class-identifier, 15, 5) = "00007" {{
		filename "uefi/ipxe.efi";
	}} else {{
		filename "bios/undionly.kpxe";
	}}
	next-server {next_server};
"""

_auto_discovery_pool_template = """
	pool {{
		max-lease-time 900;
		default-lease-time 900;

		range {start_ip} {end_ip};
	}}
"""


_host_template = """
	host {hostname} {{
		hardware ethernet {mac_address};
		fixed-address {ip_address};
	}}"""


_subnet_template = """
subnet {net_address} netmask {net_mask} {{
	{dns_servers}
	{routers}
	{pool}
	{hosts}
	{netboot}
}}
"""


def rfc3442_static_routes(routes: tp.List[StaticRoute]) -> str:
    if len(routes) == 0:
        return ""

    route_line = ""
    rfc3442_route_line = "option rfc3442-classless-static-routes"

    for route in routes:
        if route.is_default:
            continue
        rfc3442_route_line += route.to_rfc3442()

    # Only single route is supported as default
    if default_route := next((r for r in routes if r.is_default), None):
        route_line = f"option routers {str(default_route.via)};"
        rfc3442_route_line += default_route.to_rfc3442()

    rfc3442_route_line = rfc3442_route_line[:-1] + ";"
    return f"{route_line}\n\t{rfc3442_route_line}\n"


def dhcp_config(subnets: tp.Dict[models.Subnet, tp.List[models.Port]]) -> str:
    # FIXME(akremenetsky): It's considered the subnets aren't intersecting

    config = _common_settings

    # Build every subnet
    for subnet, ports in subnets.items():
        if discovery_range := subnet.ip_discovery_range_pair:
            start_ip, end_ip = discovery_range
            pool = _auto_discovery_pool_template.format(
                start_ip=str(start_ip),
                end_ip=str(end_ip),
            )
        else:
            pool = ""

        hosts = ""
        for port in ports:
            if port.mac is None or port.ipv4 is None:
                raise ValueError("Port is not configured")

            hosts += _host_template.format(
                mac_address=port.mac,
                ip_address=port.ipv4,
                hostname=f"P_{str(port.uuid)}",
            )

        if subnet.next_server and ";" not in subnet.next_server:
            netboot = _netboot_template.format(next_server=subnet.next_server)
        else:
            netboot = ""

        # An option with no value is a syntax error, and dhcpd refuses the
        # whole file over one: a subnet that names no resolver — a pool of
        # addresses nothing is placed in, for instance — took the PXE server
        # down for every network on the host, and the only sign of it was
        # guests looping on "No configuration methods succeeded".
        dns_servers = (
            "option domain-name-servers %s;" % ",".join(subnet.dns_servers)
            if subnet.dns_servers
            else ""
        )

        routes = [StaticRoute(**r) for r in subnet.routers]

        config += _subnet_template.format(
            net_address=str(subnet.cidr.network),
            net_mask=str(subnet.cidr.netmask),
            dns_servers=dns_servers,
            routers=rfc3442_static_routes(routes),
            pool=pool,
            hosts=hosts,
            netboot=netboot,
        )

    return config
