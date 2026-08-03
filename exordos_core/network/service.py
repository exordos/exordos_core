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
import uuid as sys_uuid

from gcl_looper.services import basic
from gcl_sdk.agents.universal.drivers import pool as ua_pool
import netaddr
from restalchemy.common import contexts
from restalchemy.dm import filters as dm_filters

from exordos_core.compute import constants as nc
from exordos_core.compute.dm import models
from exordos_core.network import ipam as net_ipam
from exordos_core.network.dm import models as net_models
from exordos_core.network.driver import base as net_base

LOG = logging.getLogger(__name__)

# The interface whose address the node publishes as its own.
PRIMARY_INDEX = 0


class PlacementError(Exception):
    """A node asked for a leg that cannot be built as asked.

    The caller's to fix, so it is logged rather than retried.
    """


class NetworkService(basic.BasicService):
    def _get_vm_nodes(self) -> tp.List[models.Node]:
        """Every VM node: one with some legs built may still lack others."""
        return models.Node.objects.get_all(
            filters={"node_type": dm_filters.EQ(ua_pool.NodeType.VM.value)}
        )

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
                        # Keeps the subnet's project, not the node's; the
                        # ownership check exempts it by provenance.
                        provenance=nc.PortProvenance.GENERATED.value,
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
        network: models.Network,
        subnet_map: tp.Dict[net_models.Subnet, tp.List[net_models.Port]],
    ) -> None:
        driver: net_base.AbstractNetworkDriver = network.load_driver()
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

            # Refresh generated services and default group on every pass,
            # so an upgraded default reaches subnets with no port work too.
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
        target_ports = {p.uuid: p for p in target_ports}

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
                # `default_network` is the primary leg's port and no other.
                if target_port.index == PRIMARY_INDEX:
                    target_port.node.update_default_network(p)
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

        # Actualize ports
        for uuid in actual_ports.keys() & target_ports.keys():
            target_port = target_ports[uuid]
            actual_port = actual_ports[uuid]

            # Actualize a small set of fields so far, plus whatever else the
            # driver considers part of the port (see `port_is_stale`).
            if (
                target_port.ipv4 != actual_port.ipv4
                or target_port.mask != actual_port.mask
                or driver.port_is_stale(target_port.cast_to_base())
            ):
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
                    # Update `default_network` for the node as well
                    if target_port.index == PRIMARY_INDEX:
                        target_port.node.update_default_network(actual_port)
                    target_port.update()
                except Exception:
                    LOG.exception(
                        "Unable to actualize status of port %s",
                        actual_port.uuid,
                    )

    @staticmethod
    def _is_placeable(subnet: net_models.Subnet) -> bool:
        """Whether a node can sit on this subnet at all.

        Not the boot subnet (dirty hack until boot networking is modelled),
        nor an address pool.
        """
        if subnet.next_server is not None:
            return False
        return getattr(subnet, "placeable", True)

    def _resolve_subnet(
        self,
        interface: models.NodeInterfaceOnSubnetKind,
        subnet_map: tp.Dict[net_models.Subnet, tp.List[net_models.Port]],
        strict: bool = True,
    ) -> net_models.Subnet:
        """Which subnet this leg goes on.

        A network with several placeable subnets is refused rather than
        resolved by row order, which would differ between installations.
        Not ``strict`` for a legacy pin: the node is already placed that way
        on live installations, so ambiguity only warns and the first wins.
        """
        if interface.subnet is not None:
            for subnet in subnet_map:
                if str(subnet.uuid) == str(interface.subnet):
                    return subnet
            raise PlacementError(f"subnet {interface.subnet} does not exist")

        candidates = [
            subnet
            for subnet in subnet_map
            if str(getattr(subnet.network, "uuid", subnet.network))
            == str(interface.network)
            and self._is_placeable(subnet)
        ]
        if not candidates:
            raise PlacementError(
                f"network {interface.network} has no subnet a node can sit on"
            )
        if len(candidates) > 1:
            listed = ", ".join(sorted(str(s.uuid) for s in candidates))
            message = (
                f"network {interface.network} has {len(candidates)} placeable "
                f"subnets ({listed}); name the one you mean with `subnet`"
            )
            if strict:
                raise PlacementError(message)
            LOG.warning("Legacy pin resolved by order: %s", message)
        return candidates[0]

    def _auto_subnet(
        self,
        subnet_map: tp.Dict[net_models.Subnet, tp.List[net_models.Port]],
    ) -> net_models.Subnet:
        """Where a node that declared nothing lands (the legacy rule).

        Overlays stay opt-in: iteration order is not deterministic, and a
        management node on an overlay has no connectivity until it is wired.
        """
        for subnet in subnet_map:
            if not self._is_placeable(subnet):
                continue
            if not nc.is_overlay(getattr(subnet.network, "driver_spec", None)):
                return subnet
        raise PlacementError("no subnet an undeclared node can sit on")

    @staticmethod
    def _leg_project(node: models.Node) -> sys_uuid.UUID:
        """The project a node's legs belong to.

        For a set-generated node, the set owner's: the node lives in the set
        project, but what its legs name belongs to the owner.
        """
        if node.node_set is None or node.project_id != nc.NODE_SET_PROJECT:
            return node.project_id
        node_set = models.NodeSet.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(node.node_set)}
        )
        return node_set.project_id if node_set is not None else node.project_id

    def _build_port(
        self,
        node: models.Node,
        subnet: net_models.Subnet,
        index: int,
        target_ip: tp.Optional[netaddr.IPAddress],
        config: tp.Optional[tp.Any],
        port_security: bool,
        ipam: net_ipam.Ipam,
        subnet_map: tp.Dict[net_models.Subnet, tp.List[net_models.Port]],
    ) -> net_models.Port:
        ip = ipam.allocate_ip(subnet, target_ip)
        mask = subnet.cidr.netmask
        fields = dict(
            target_ipv4=target_ip,
            target_mask=mask if target_ip else None,
            ipv4=ip,
            mask=mask,
            node=node.uuid,
            mac=models.Port.generate_mac(),
            project_id=self._leg_project(node),
            subnet=subnet.uuid,
            source=subnet.name,
            index=index,
            # The compiler may rewrite and remove it.
            provenance=nc.PortProvenance.GENERATED.value,
            port_security=port_security,
        )
        if config is not None:
            fields["config"] = config
        port = models.Port(**fields)
        net_port = net_models.Port.restore_from_simple_view(
            **port.dump_to_simple_view()
        )

        subnet_map[subnet].append(net_port)

        return net_port

    @staticmethod
    def _legacy_interface(
        node: models.Node,
    ) -> tp.Optional[models.NodeInterfaceOnSubnetKind]:
        """The leg a pre-`interfaces` node asked for, if it asked for one.

        `default_network.network` and `target_ipv4` are still honoured for one
        release: sealing the field would 400 existing manifests on upgrade.
        """
        legacy = node.default_network or {}
        network = legacy.get("network")
        address = legacy.get("target_ipv4")
        if not network and not address:
            return None
        LOG.warning(
            "Node %s places itself with `default_network`, which is "
            "deprecated and will stop being read; declare `interfaces` "
            "instead (see docs/network/node-interfaces.md)",
            node.uuid,
        )
        return models.NodeInterfaceOnSubnetKind(
            network=sys_uuid.UUID(str(network)) if network else None,
            address=netaddr.IPAddress(address) if address else None,
        )

    def _adopt_port(
        self,
        node: models.Node,
        interface: models.NodeInterfaceOnPortKind,
        index: int,
    ) -> None:
        """Take an existing port as one of this node's legs.

        The port keeps its own configuration and provenance (see
        `NodeInterfaceOnPortKind`).
        """
        port = net_models.Port.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(interface.port)}
        )
        if port is None:
            raise PlacementError(f"port {interface.port} does not exist")
        if str(port.project_id) != str(node.project_id):
            raise PlacementError(f"port {interface.port} belongs to another project")
        attached = getattr(port.node, "uuid", port.node)
        if attached is not None and str(attached) != str(node.uuid):
            raise PlacementError(
                f"port {interface.port} is already a leg of node {attached}"
            )
        if str(attached) == str(node.uuid) and port.index == index:
            return
        port.node = node.uuid
        port.index = index
        port.update()

    def _leg_holds(
        self,
        port: net_models.Port,
        interface: tp.Any,
        subnet_map: tp.Dict[net_models.Subnet, tp.List[net_models.Port]],
    ) -> bool:
        """Whether a built leg still is the one declared at its index.

        False when it has to be rebuilt; config and port security are
        updated in place.
        """
        if interface.KIND != "subnet":
            return False
        # A port read from the database carries its subnet's row.
        built_on = str(getattr(port.subnet, "uuid", port.subnet))
        if interface.subnet is not None:
            if built_on != str(interface.subnet):
                return False
        else:
            # Any subnet of the network will do: re-resolving would refuse a
            # built leg once the network gains a second placeable subnet.
            on = next((sn for sn in subnet_map if str(sn.uuid) == built_on), None)
            if on is None or str(getattr(on.network, "uuid", on.network)) != str(
                interface.network
            ):
                return False
        if interface.address is not None and str(port.ipv4) != str(interface.address):
            return False
        config = interface.config.dump_to_simple_view()
        current = port.config.dump_to_simple_view() if port.config else None
        if current != config or port.port_security != interface.port_security:
            port.config = interface.config
            port.port_security = interface.port_security
            port.update()
        return True

    def _reconcile_interfaces(
        self,
        node: models.Node,
        existing: tp.List[net_models.Port],
        ipam: net_ipam.Ipam,
        subnet_map: tp.Dict[net_models.Subnet, tp.List[net_models.Port]],
    ) -> None:
        """Bring a node's ports in line with what it declared.

        Only generated ports are added and removed here, so the declaration
        never fights the ports API.
        """
        if node.interfaces is None:
            # Legacy placement: one port, and only for a node that has none.
            if existing:
                return
            legacy = self._legacy_interface(node)
            self._build_port(
                node=node,
                subnet=(
                    self._resolve_subnet(legacy, subnet_map, strict=False)
                    if legacy is not None and legacy.network is not None
                    else self._auto_subnet(subnet_map)
                ),
                index=PRIMARY_INDEX,
                target_ip=legacy.address if legacy is not None else None,
                config=None,
                port_security=True,
                ipam=ipam,
                subnet_map=subnet_map,
            ).insert()
            return

        wanted = {}
        for position, interface in enumerate(node.interfaces):
            index = interface.index if interface.index is not None else position
            wanted[index] = interface

        generated = {
            port.index: port
            for port in existing
            if port.provenance == nc.PortProvenance.GENERATED.value
        }

        # Undeclared legs go; the data plane follows on the next pass.
        for index, port in generated.items():
            if index not in wanted:
                port.delete()

        for index, interface in sorted(wanted.items()):
            built = generated.get(index)
            if built is not None and self._leg_holds(built, interface, subnet_map):
                continue
            if built is not None:
                # A leg's placement is its identity: changed means rebuilt.
                built.delete()
            if interface.KIND == "port":
                self._adopt_port(node, interface, index)
                continue
            subnet = self._resolve_subnet(interface, subnet_map)
            self._build_port(
                node=node,
                subnet=subnet,
                index=index,
                target_ip=(
                    netaddr.IPAddress(interface.address) if interface.address else None
                ),
                config=interface.config,
                port_security=interface.port_security,
                ipam=ipam,
                subnet_map=subnet_map,
            ).insert()

    def _iteration(self) -> None:
        ipam = None

        with contexts.Context().session_manager():
            vm_nodes = self._get_vm_nodes()
            subnet_map = self._get_subnet_map()
            new_hw_ports = self._get_new_hw_ports(subnet_map.keys())
            network_map = self._build_network_map(subnet_map)

            ports_by_node = collections.defaultdict(list)
            for ports in subnet_map.values():
                for port in ports:
                    attached = getattr(port.node, "uuid", port.node)
                    if attached is not None:
                        ports_by_node[str(attached)].append(port)

            for node in vm_nodes:
                # Initialize ipam if needed
                if ipam is None:
                    ipam = net_ipam.Ipam(
                        subnet_map, net_ipam.ledger_reservations(subnet_map)
                    )

                try:
                    self._reconcile_interfaces(
                        node,
                        ports_by_node.get(str(node.uuid), []),
                        ipam,
                        subnet_map,
                    )
                except PlacementError as e:
                    LOG.error(
                        "Cannot give node %s the interfaces it asked for: %s",
                        node.uuid,
                        e,
                    )
                except Exception:
                    LOG.exception("Error allocating ports for node %s", node.uuid)

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
            for network, net_subnet_map in network_map.items():
                try:
                    self._actualize_network(network, net_subnet_map)
                except Exception:
                    LOG.exception("Error actualizing network %s", network.uuid)
