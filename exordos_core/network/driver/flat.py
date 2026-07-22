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
import dataclasses
import json
import logging
import os
import subprocess
import typing as tp
import uuid as sys_uuid

from gcl_sdk.agents.universal.drivers import pool as ua_pool

from exordos_core.compute.dm import models
from exordos_core.network import exceptions
from exordos_core.network.dhcp import isc
from exordos_core.network.driver import base

DHCP_CTX_FILE = "gc_ctx_dhcpd.json"

LOG = logging.getLogger(__name__)


class InvalidFlatDriverSpec(exceptions.CGNetException):
    __template__ = "Invalid flat network driver spec: {spec}"
    spec: dict


class DhcpCfgNotFound(exceptions.CGNetException):
    __template__ = "Dhcp config file not found: {cfg}"
    cfg: str


class InvalidDhcpCtx(exceptions.CGNetException):
    __template__ = "Invalid dhcp context: {cfg}"
    cfg: str


class DhcpSubnetAlreadyExists(exceptions.CGNetException):
    __template__ = "Dhcp subnet already exists: {subnet}"
    subnet: sys_uuid.UUID


class DhcpPortAlreadyExists(exceptions.CGNetException):
    __template__ = "Dhcp port already exists: {port}"
    port: sys_uuid.UUID


@dataclasses.dataclass
class DHCPContext:
    cfg_hash: str
    subnets: tp.List[models.Subnet]
    port_map: tp.DefaultDict[sys_uuid.UUID, tp.List[models.Port]]

    @property
    def subnet_map(self) -> tp.Dict[models.Subnet, tp.List[models.Port]]:
        return {s: self.port_map[s.uuid] for s in self.subnets}

    def save_ctx(self, ctx_path: str) -> None:
        with open(ctx_path, "w") as fctx:
            port_map = {}
            for uuid, ports in self.port_map.items():
                port_map[str(uuid)] = [p.dump_to_simple_view() for p in ports]

            data = {
                "cfg_hash": self.cfg_hash,
                "subnets": [s.dump_to_simple_view() for s in self.subnets],
                "port_map": port_map,
            }
            json.dump(data, fctx, indent=2)

        LOG.debug("Saved dhcp context to %s, hash: %s", ctx_path, self.cfg_hash)

    def is_empty(self) -> bool:
        return self.cfg_hash == ""

    @classmethod
    def load_ctx(cls, ctx_path: str, empty_if_missing: bool = True) -> "DHCPContext":
        if not os.path.exists(ctx_path):
            if empty_if_missing:
                return cls.fill_empty_ctx(ctx_path, force=True)

            raise FileNotFoundError()

        with open(ctx_path) as fctx:
            data = json.load(fctx)

        cfg_hash = data["cfg_hash"]
        subnets = [models.Subnet.restore_from_simple_view(**s) for s in data["subnets"]]
        port_map = collections.defaultdict(list)
        for uuid, ports in data["port_map"].items():
            port_map[sys_uuid.UUID(uuid)] = [
                models.Port.restore_from_simple_view(**p) for p in ports
            ]

        return cls(subnets=subnets, port_map=port_map, cfg_hash=cfg_hash)

    @classmethod
    def fill_empty_ctx(cls, ctx_path: str, force: bool = False) -> "DHCPContext":
        if not force and os.path.exists(ctx_path):
            raise FileExistsError()

        ctx = cls.get_empty_ctx()
        ctx.save_ctx(ctx_path)
        return ctx

    @classmethod
    def get_empty_ctx(cls) -> "DHCPContext":
        port_map = collections.defaultdict(list)
        return cls(subnets=[], port_map=port_map, cfg_hash="")

    def add_subnet(self, subnet: models.Subnet) -> None:
        if subnet in self.subnets:
            raise DhcpSubnetAlreadyExists(subnet=subnet.uuid)

        self.subnets.append(subnet)
        self.port_map[subnet.uuid] = []

    def delete_subnet(self, subnet: models.Subnet) -> None:
        if subnet not in self.subnets:
            return

        self.subnets.remove(subnet)
        del self.port_map[subnet.uuid]

    def add_port(self, port: models.Port) -> None:
        if any(p.uuid == port.uuid for p in self.port_map[port.subnet]):
            raise DhcpPortAlreadyExists(port=port.uuid)

        self.port_map[port.subnet].append(port)

    def delete_port(self, port: models.Port) -> None:
        if not any(p.uuid == port.uuid for p in self.port_map[port.subnet]):
            return

        self.port_map[port.subnet].remove(port)


class FlatBridgeNetworkDriver(base.AbstractNetworkDriver):
    DRIVER_NAME = "flat_bridge"

    def __init__(self, network: models.Network) -> None:
        spec = network.driver_spec
        if (
            "driver" not in spec
            or spec["driver"] != self.DRIVER_NAME
            or "dhcp_cfg" not in spec
        ):
            raise InvalidFlatDriverSpec(spec=spec)

        self._dhcp_cfg_path = spec["dhcp_cfg"]
        self._dhcp_ctx_path = os.path.join(
            os.path.dirname(self._dhcp_cfg_path), DHCP_CTX_FILE
        )

        if not os.path.exists(self._dhcp_cfg_path):
            raise DhcpCfgNotFound(cfg=self._dhcp_cfg_path)

        if not os.path.exists(self._dhcp_ctx_path):
            DHCPContext.fill_empty_ctx(self._dhcp_ctx_path)

    def _load_ctx(self) -> DHCPContext:
        # If the configuration isn't valid. Consider it as empty in this
        # case in order to rebuild the networks.
        try:
            ctx = DHCPContext.load_ctx(self._dhcp_ctx_path)
        except json.decoder.JSONDecodeError:
            return DHCPContext.get_empty_ctx()

        if self._cfg_hash() != ctx.cfg_hash:
            return DHCPContext.get_empty_ctx()

        return ctx

    def _cfg_hash(self) -> str:
        with open(self._dhcp_cfg_path) as fcfg:
            return str(hash(fcfg.read()))

    def _save_cfg(self, content: str) -> None:
        with open(self._dhcp_cfg_path, "w") as fcfg:
            fcfg.write(content)

    def _reload_dhcp_service(self) -> None:
        # isc-dhcp unable to relaod the configuration by HUP signal
        # just to restart the service
        subprocess.check_call(["systemctl", "restart", isc.DHCP_ISC_SVC_NAME])

    def _apply_cfg(self, ctx: DHCPContext) -> None:
        dhcp_config = isc.dhcp_config(ctx.subnet_map)
        self._save_cfg(dhcp_config)
        self._reload_dhcp_service()
        ctx.cfg_hash = self._cfg_hash()
        ctx.save_ctx(self._dhcp_ctx_path)

    def list_subnets(self) -> tp.Iterable[models.Subnet]:
        """Return subnet list from data plane."""
        ctx = self._load_ctx()
        return ctx.subnets

    def list_ports(self, subnet: models.Subnet) -> tp.Iterable[models.Port]:
        """Return port list from data plane."""
        ctx = self._load_ctx()
        return ctx.port_map[subnet.uuid]

    def create_subnet(self, subnet: models.Subnet) -> models.Subnet:
        """Create a new subnet."""
        ctx = self._load_ctx()

        for s in ctx.subnets:
            if s.uuid == subnet.uuid:
                raise DhcpSubnetAlreadyExists(subnet=subnet.uuid)

        # Add a new subnet, rebuild configuration, reload the service
        ctx.add_subnet(subnet)
        self._apply_cfg(ctx)
        LOG.info(
            "Enabled subnet %s into DHCP configuration %s",
            subnet.uuid,
            self._dhcp_cfg_path,
        )

        return subnet

    def create_port(self, port: models.Port) -> models.Port:
        """Create a new port."""
        ctx = self._load_ctx()

        for p in ctx.port_map[port.subnet]:
            if p.uuid == port.uuid:
                raise DhcpPortAlreadyExists(port=port.uuid)

        # Add a new port, rebuild configuration, reload the service
        port.status = ua_pool.PortStatus.ACTIVE.value
        try:
            ctx.add_port(port)
            self._apply_cfg(ctx)
        except Exception:
            port.status = ua_pool.PortStatus.NEW.value
            raise

        LOG.info(
            "Enabled port %s into DHCP configuration %s",
            port.uuid,
            self._dhcp_cfg_path,
        )

        return port

    def create_ports(self, ports: tp.List[models.Port]) -> tp.List[models.Port]:
        """Create a list of ports."""
        ctx = self._load_ctx()
        new_ports = []

        for port in ports:
            for p in ctx.port_map[port.subnet]:
                if p.uuid == port.uuid:
                    raise DhcpPortAlreadyExists(port=port.uuid)

            port.status = ua_pool.PortStatus.ACTIVE.value
            ctx.add_port(port)
            new_ports.append(port)

        # Add a new port, rebuild configuration, reload the service
        try:
            self._apply_cfg(ctx)
        except Exception:
            for port in ports:
                port.status = ua_pool.PortStatus.NEW.value
            raise

        LOG.info(
            "Enabled ports %s into DHCP configuration %s",
            [p.uuid for p in ports],
            self._dhcp_cfg_path,
        )

        return new_ports

    def delete_subnet(self, subnet: models.Subnet) -> None:
        """Delete the subnet from data plane."""
        ctx = self._load_ctx()

        for s in ctx.subnets:
            if s.uuid != subnet.uuid:
                continue

            ctx.delete_subnet(s)
            self._apply_cfg(ctx)
            LOG.info(
                "Disabled subnet %s from DHCP configuration %s",
                subnet.uuid,
                self._dhcp_cfg_path,
            )

            return

        LOG.warning(
            "Subnet %s not found in DHCP configuration %s",
            subnet.uuid,
            self._dhcp_cfg_path,
        )

    def delete_port(self, port: models.Port) -> None:
        """Delete the port from data plane."""
        ctx = self._load_ctx()

        for p in ctx.port_map[port.subnet]:
            if p.uuid != port.uuid:
                continue

            ctx.delete_port(p)
            self._apply_cfg(ctx)
            LOG.info(
                "Disabled port %s from DHCP configuration %s",
                port.uuid,
                self._dhcp_cfg_path,
            )

            return

        LOG.warning(
            "Port %s not found in DHCP configuration %s",
            port.uuid,
            self._dhcp_cfg_path,
        )

    def delete_ports(self, ports: tp.List[models.Port]) -> None:
        ctx = self._load_ctx()

        for port in ports:
            for p in ctx.port_map[port.subnet]:
                if p.uuid != port.uuid:
                    continue

                ctx.delete_port(p)

        self._apply_cfg(ctx)

        LOG.info(
            "Disabled ports %s from DHCP configuration %s",
            [p.uuid for p in ports],
            self._dhcp_cfg_path,
        )

    def update_port(self, port: models.Port) -> models.Port:
        """Update the port in data plane."""
        ctx = self._load_ctx()

        # it's equivalent to replace
        ctx.delete_port(port)

        port.status = ua_pool.PortStatus.ACTIVE.value
        try:
            ctx.add_port(port)
            self._apply_cfg(ctx)
        except Exception:
            port.status = ua_pool.PortStatus.NEW.value
            raise

        LOG.info(
            "Updated port %s in DHCP configuration %s",
            port.uuid,
            self._dhcp_cfg_path,
        )

        return port

    def update_subnet(self, subnet: models.Subnet) -> models.Subnet:
        """Update the subnet in data plane."""
        ctx = self._load_ctx()

        # it's equivalent to replace
        # The ports will be added on a next iteration
        ctx.delete_subnet(subnet)
        ctx.add_subnet(subnet)
        self._apply_cfg(ctx)

        LOG.info(
            "Updated subnet %s in DHCP configuration %s",
            subnet.uuid,
            self._dhcp_cfg_path,
        )

        return subnet
