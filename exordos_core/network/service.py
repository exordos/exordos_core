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

import collections
import logging
import typing as tp

from gcl_looper.services import basic
from restalchemy.common import contexts
from restalchemy.dm import filters as dm_filters

from gcl_sdk.agents.universal.dm import models as ua_models

from exordos_core.compute.dm import models
from exordos_core.network import ipam as net_ipam
from exordos_core.network.dm import models as net_models
from exordos_core.network.driver import base as net_base

LOG = logging.getLogger(__name__)


class NetworkService(basic.BasicService):
    def _get_new_hw_ports(
        self, subnets: tp.Iterable[net_models.Subnet]
    ) -> tp.List[net_models.Port]:
        ports = []
        nodes = net_models.HWNodeWithoutPorts.get_nodes()

        if not nodes:
            return ports

        # A bare-metal node has a row per addressed interface; each of its
        # ports takes the next device slot.
        indexes = collections.Counter()

        # Prepare ports for HW nodes.
        # Need to detect the corresponding subnets
        for node in nodes:
            for subnet in subnets:
                if node.iface.ipv4 in subnet.cidr:
                    # Create ports for HW nodes
                    port = net_models.Port(
                        subnet=subnet,
                        mac=node.iface.mac,
                        project_id=subnet.project_id,
                        node=node.node,
                        machine=node.machine,
                        mask=subnet.cidr.netmask,
                        source=subnet.name,
                        index=indexes[node.node],
                        owner_node=getattr(node.node, "uuid", node.node),
                    )
                    indexes[node.node] += 1
                    ports.append(port)

        return ports

    def _get_subnet_map(
        self,
    ) -> tp.Dict[net_models.Subnet, tp.List[net_models.Port]]:
        # TODO(akremenetsky): Take all subnets so far.
        # This snippet will be reworked.
        subnets = net_models.Subnet.objects.get_all()
        ports = net_models.Port.objects.get_all(
            filters={
                "subnet": dm_filters.In([str(subnet.uuid) for subnet in subnets]),
            }
        )

        port_subnet_map = collections.defaultdict(list)
        for port in ports:
            port_subnet_map[port.subnet.uuid].append(port)

        # A subnet can be created without any ports. In this case
        # we create an empty list for this subnet so that the
        # actualization logic will work correctly.
        subnet_map = {}
        for subnet in subnets:
            if subnet.uuid in port_subnet_map:
                subnet_map[subnet] = port_subnet_map[subnet.uuid]
            else:
                subnet_map[subnet] = []

        return subnet_map

    def _build_network_map(
        self, subnet_map: tp.Dict[net_models.Subnet, tp.List[net_models.Port]]
    ) -> tp.DefaultDict[
        models.Network, tp.Dict[net_models.Subnet, tp.List[net_models.Port]]
    ]:
        network_map = collections.defaultdict(dict)

        # Every network, including those whose last subnet was deleted, so
        # the driver can clean up what the subnet left on the hypervisors.
        for network in models.Network.objects.get_all():
            network_map[network] = {}

        for subnet in subnet_map.keys():
            network_map[subnet.network][subnet] = subnet_map[subnet]

        return network_map

    def _actualize_network(
        self,
        driver: net_base.AbstractNetworkDriver,
        subnet_map: tp.Dict[net_models.Subnet, tp.List[net_models.Port]],
    ) -> None:
        actual_subnets = {s.uuid: s for s in driver.list_subnets()}
        target_subnets = {s.uuid: s for s in subnet_map.keys()}

        # Create subnets
        for uuid in target_subnets.keys() - actual_subnets.keys():
            subnet = target_subnets[uuid]
            try:
                driver.create_subnet(subnet.cast_to_base())
            except Exception:
                LOG.exception("Error creating subnet %s", subnet.uuid)

        # Delete subnets
        for uuid in actual_subnets.keys() - target_subnets.keys():
            subnet = actual_subnets[uuid]
            try:
                driver.delete_subnet(subnet)
            except Exception:
                LOG.exception("Error deleting subnet %s", subnet.uuid)

        # Actualize subnets
        for uuid in actual_subnets.keys() & target_subnets.keys():
            target_subnet = target_subnets[uuid]
            actual_subnet = actual_subnets[uuid]
            target_ports = subnet_map[target_subnet]

            # Seed missing service rows even when port wiring is unchanged.
            try:
                driver.refresh_generated(target_subnet.cast_to_base())
            except Exception:
                LOG.exception(
                    "Error refreshing the generated objects of subnet %s", uuid
                )

            try:
                self._actualize_subnet(
                    driver,
                    actual_subnet,
                    target_subnet,
                    target_ports,
                )
            except Exception:
                LOG.exception("Error actualizing subnet %s", actual_subnet.uuid)

    def _actualize_subnet(
        self,
        driver: net_base.AbstractNetworkDriver,
        actual_subnet: models.Subnet,
        target_subnet: net_models.Subnet,
        target_ports: tp.List[net_models.Port],
    ) -> None:
        actual_ports = {p.uuid: p for p in driver.list_ports(actual_subnet)}
        target_ports = {p.uuid: p for p in target_ports if p.node is not None}

        if (
            target_subnet.cidr != actual_subnet.cidr
            or target_subnet.next_server != actual_subnet.next_server
        ):
            try:
                subnet = driver.update_subnet(target_subnet.cast_to_base())
                target_subnet.cidr = subnet.cidr
                target_subnet.next_server = subnet.next_server
                target_subnet.update()
            except Exception:
                LOG.exception("Error actualizing subnet %s", actual_subnet.uuid)

        # Create ports
        ports = tuple(
            target_ports[u].cast_to_base()
            for u in target_ports.keys() - actual_ports.keys()
        )
        try:
            if ports:
                ports = driver.create_ports(ports)
        except Exception:
            LOG.exception("Error creating ports: %s", ports)
            ports = tuple()

        for p in ports:
            target_port = target_ports[p.uuid]
            target_port.status = p.status
            target_port.ipv4 = p.ipv4
            target_port.mask = p.mask
            target_port.mac = p.mac
            target_port.interface = p.interface

            try:
                target_port.update()
            except Exception:
                LOG.exception("Error creating port %s", target_port.uuid)

        # Delete ports
        ports = tuple(
            actual_ports[u] for u in actual_ports.keys() - target_ports.keys()
        )
        try:
            if ports:
                driver.delete_ports(ports)
        except Exception:
            LOG.exception("Error creating ports: %s", ports)
            ports = tuple()

        common_ports = actual_ports.keys() & target_ports.keys()
        with driver.compare_ports(
            target_subnet.cast_to_base(),
            (target_ports[u] for u in common_ports),
        ):
            stale_ports = {
                uuid
                for uuid in common_ports
                if target_ports[uuid].ipv4 != actual_ports[uuid].ipv4
                or target_ports[uuid].mask != actual_ports[uuid].mask
                or driver.port_is_stale(target_ports[uuid].cast_to_base())
            }

        # Actualize ports
        for uuid in common_ports:
            target_port = target_ports[uuid]
            actual_port = actual_ports[uuid]
            if uuid in stale_ports:
                try:
                    driver.update_port(target_port.cast_to_base())
                except Exception:
                    LOG.exception("Error actualizing port %s", actual_port.uuid)

            # Actualize status
            if (
                target_port.status != actual_port.status
                or target_port.ipv4 != actual_port.ipv4
                or target_port.mask != actual_port.mask
            ):
                target_port.status = actual_port.status
                target_port.ipv4 = actual_port.ipv4
                target_port.mask = actual_port.mask

                try:
                    target_port.update()
                except Exception:
                    LOG.exception(
                        "Unable to actualize status of port %s",
                        actual_port.uuid,
                    )

    def _collect_orphan_nf_targets(self, subnet_map) -> None:
        subnets = {subnet.uuid: subnet for subnet in subnet_map}
        serving_subnets = {
            (target.agent, target.master)
            for target in ua_models.TargetResource.objects.get_all(
                filters={"kind": dm_filters.EQ("evpn_port")}
            )
            if target.master in subnets
        }
        serving_networks = {
            (agent, subnets[subnet_uuid].network.uuid)
            for agent, subnet_uuid in serving_subnets
        }
        for target in ua_models.TargetResource.objects.get_all(
            filters={"kind": dm_filters.EQ("evpn_nf")}
        ):
            subnet = subnets.get(target.master)
            if subnet is None or (
                (target.agent, subnet.uuid) not in serving_subnets
                if target.value["kind"] == "dhcp"
                else (target.agent, subnet.network.uuid) not in serving_networks
            ):
                target.delete()

    def _iteration(self) -> None:
        ipam = None

        with contexts.Context().session_manager():
            subnet_map = self._get_subnet_map()
            self._collect_orphan_nf_targets(subnet_map)
            new_hw_ports = self._get_new_hw_ports(subnet_map.keys())
            network_map = self._build_network_map(subnet_map)

            # There are new HW ports. Allocate IPs to them.
            for port in new_hw_ports:
                # Initialize ipam if needed
                if ipam is None:
                    ipam = net_ipam.Ipam(
                        subnet_map, net_ipam.ledger_reservations(subnet_map)
                    )
                try:
                    port.ipv4 = ipam.allocate_ip(port.subnet)
                    port.insert()
                    subnet_map[port.subnet].append(port)
                except Exception:
                    LOG.exception("Error allocating IP for machine %s", port.machine)

            # Actualize ports and subnets on the data plane
            driver_ready = {}
            for network, net_subnet_map in network_map.items():
                try:
                    driver = network.load_driver()
                    driver_type = type(driver)
                    if driver_type not in driver_ready:
                        driver_ready[driver_type] = False
                        driver.refresh_global()
                        driver_ready[driver_type] = True
                    if driver_ready[driver_type]:
                        with driver.reconcile():
                            self._actualize_network(driver, net_subnet_map)
                except Exception:
                    LOG.exception("Error actualizing network %s", network.uuid)
