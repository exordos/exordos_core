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

import abc
import typing as tp

from exordos_core.compute.dm import models


class AbstractNetworkDriver(abc.ABC):
    def bind(self, network: models.Network) -> None:
        """Point a cached driver instance at the network row just loaded.

        Drivers are cached per network so the entry point is resolved once,
        but the model they hold is a snapshot: without rebinding, editing any
        network field the cache key does not cover would never reach the
        compiler.
        """

    def refresh_generated(self, subnet: models.Subnet) -> None:
        """Bring what the installation generates for this subnet up to date.

        Optional: a driver that generates nothing needs no sweep. For those
        that do, this is called every iteration rather than only when a port
        is compiled — otherwise an upgrade that changes a default reaches a
        busy installation and never a quiet one.
        """

    @abc.abstractmethod
    def list_subnets(self) -> tp.Iterable[models.Subnet]:
        """Return subnet list from data plane."""

    @abc.abstractmethod
    def list_ports(self, subnet: models.Subnet) -> tp.Iterable[models.Port]:
        """Return port list from data plane."""

    @abc.abstractmethod
    def create_subnet(self, subnet: models.Subnet) -> models.Subnet:
        """Create a new subnet."""

    @abc.abstractmethod
    def create_port(self, port: models.Port) -> models.Port:
        """Create a new port."""

    @abc.abstractmethod
    def delete_subnet(self, subnet: models.Subnet) -> None:
        """Delete the subnet from data plane."""

    @abc.abstractmethod
    def delete_port(self, port: models.Port) -> None:
        """Delete the port from data plane."""

    @abc.abstractmethod
    def update_port(self, port: models.Port) -> models.Port:
        """Update the port in data plane."""

    @abc.abstractmethod
    def update_subnet(self, subnet: models.Subnet) -> models.Subnet:
        """Update the subnet in data plane."""

    def port_is_stale(self, port: models.Port, actual_port: models.Port) -> bool:
        """True when the port's data plane no longer matches the model.

        The service actualizes a port when its address changed. A driver
        whose port carries more than an address — filtering, network
        functions, floating addresses, all of which live in objects the port
        only references — says so here, otherwise editing any of them would
        reach the data plane no earlier than the port's next re-creation.
        """
        return False

    def create_ports(self, ports: tp.List[models.Port]) -> tp.List[models.Port]:
        """Create a list of ports."""

        # The default implementation is to create each port separately
        new_ports = []
        for port in ports:
            new_ports.append(self.create_port(port))
        return new_ports

    def delete_ports(self, ports: tp.List[models.Port]) -> None:
        """Delete the port from data plane."""

        # The default implementation is to delete each port separately
        for port in ports:
            self.delete_port(port)


class DummyNetworkDriver(AbstractNetworkDriver):
    SPEC = {"driver": "dummy"}

    def __init__(self, network: models.Network) -> None:
        if network.driver_spec != self.SPEC:
            raise ValueError(f"Invalid driver spec: {network.driver_spec}")

    def list_subnets(self) -> tp.Iterable[models.Subnet]:
        """Return subnet list from data plane."""
        return []

    def list_ports(self, subnet: models.Subnet) -> tp.Iterable[models.Port]:
        """Return port list from data plane."""
        return []

    def create_subnet(self, subnet: models.Subnet) -> models.Subnet:
        """Create a new subnet."""
        return subnet

    def create_port(self, port: models.Port) -> models.Port:
        """Create a new port."""
        return port

    def delete_subnet(self, subnet: models.Subnet) -> None:
        """Delete the subnet from data plane."""

    def delete_port(self, port: models.Port) -> None:
        """Delete the port from data plane."""

    def update_port(self, port: models.Port) -> models.Port:
        """Update the port in data plane."""
        return port

    def update_subnet(self, subnet: models.Subnet) -> models.Subnet:
        """Update the subnet in data plane."""
        return subnet
