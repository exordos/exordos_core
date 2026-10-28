# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0

import contextlib
import uuid

import netaddr
from restalchemy.dm import filters
from restalchemy.storage.sql import orm

from exordos_core.cloud import models as cloud_models
from exordos_core.cloud.links import CloudNetworkLink
from exordos_core.cloud.links import endpoints_for_subnet
from exordos_core.cloud.locking import provider_lock
from exordos_core.compute.dm import models
from exordos_core.network.driver import base
from exordos_core.user_api.network.dm import models as catalog


class CloudNetworkDriver(base.AbstractNetworkDriver):
    def __init__(self, network):
        if network.driver_spec.get("driver") != "cloud":
            raise ValueError("Not a cloud network")
        self.bind(network)

    def bind(self, network):
        self.network = network
        config = cloud_models.connection(
            network.driver_spec["connection"], network.project_id
        )
        self.cloud = config.load_driver()
        self.external = None

    def _network(self):
        if self.external is None:
            self.external = self.cloud.ensure_network(
                self.network.uuid, self.network.name
            )
            state = {"network_id": self.external.id}
            if self.network.driver_state != state:
                self.network.driver_state = state
                orm.SQLStorableMixin.update(self.network)
        return self.external

    @contextlib.contextmanager
    def reconcile(self):
        with provider_lock(self.cloud.config.connection):
            current = models.Network.objects.get_one(
                filters={"uuid": filters.EQ(self.network.uuid)}
            )
            self.bind(current)
            self._network()
            yield

    def _subnet(self, resource):
        subnet = self.cloud._find(
            self.cloud.subnets(self._network()), "subnet", resource
        )
        if subnet is None:
            raise ValueError(f"Cloud subnet is not ready: {resource}")
        return subnet

    def list_subnets(self):
        return [
            models.Subnet(
                uuid=self.cloud.identity(s, "subnet"),
                network=self.network.uuid,
                project_id=self.network.project_id,
                cidr=netaddr.IPNetwork(s.cidr),
                dhcp=s.is_dhcp_enabled,
            )
            for s in self.cloud.subnets(self._network())
        ]

    def create_subnet(self, subnet):
        routers = subnet.routers or []
        default = [r for r in routers if str(r["to"]) == "0.0.0.0/0"]
        attributes = {
            "is_dhcp_enabled": subnet.dhcp,
            "dns_nameservers": subnet.dns_servers or [],
            "host_routes": [
                {"destination": str(r["to"]), "nexthop": str(r["via"])}
                for r in routers
                if str(r["to"]) != "0.0.0.0/0"
            ],
        }
        attributes["gateway_ip"] = str(default[0]["via"]) if default else None
        external = self.cloud.ensure_subnet(
            subnet.uuid, self._network(), subnet.cidr, **attributes
        )
        external_network = self.network.driver_spec.get("external_network")
        if external_network:
            if not default:
                raise ValueError("An external router needs a subnet default gateway")
            self.cloud.ensure_router(subnet.uuid, external, external_network)
        return subnet

    def refresh_generated(self, subnet):
        self.update_subnet(subnet)
        external_network = self.network.driver_spec.get("external_network")
        if external_network:
            self.cloud.ensure_router(
                subnet.uuid, self._subnet(subnet.uuid), external_network
            )

    def update_subnet(self, subnet):
        external = self._subnet(subnet.uuid)
        links = [
            (e, CloudNetworkLink.objects.get_one(filters={"uuid": filters.EQ(e.link)}))
            for e in endpoints_for_subnet(subnet.uuid)
        ]
        routes = [
            {"destination": str(r["to"]), "nexthop": str(r["via"])}
            for r in subnet.routers or []
            if str(r["to"]) != "0.0.0.0/0"
        ]
        for endpoint, link in links:
            state = link.state.get(str(endpoint.uuid), {})
            if state.get("gateway_ip") and link.status != "DELETING":
                destinations = {
                    e["subnet"]
                    for e in link.endpoints
                    if e["subnet"] != str(subnet.uuid)
                }
                routes += [
                    {"destination": str(other.cidr), "nexthop": state["gateway_ip"]}
                    for other in models.Subnet.objects.get_all(
                        filters={"uuid": filters.In(list(destinations))}
                    )
                ]
        desired = {
            "is_dhcp_enabled": subnet.dhcp,
            "dns_nameservers": subnet.dns_servers or [],
            "host_routes": sorted(
                routes, key=lambda r: (r["destination"], r["nexthop"])
            ),
        }
        observed = {
            "is_dhcp_enabled": external.is_dhcp_enabled,
            "dns_nameservers": external.dns_nameservers or [],
            "host_routes": sorted(
                external.host_routes or [],
                key=lambda r: (r["destination"], r["nexthop"]),
            ),
        }
        if desired != observed:
            self.cloud.api.network.update_subnet(external.id, **desired)
        return subnet

    def delete_subnet(self, subnet):
        external = self.cloud._find(
            self.cloud.subnets(self._network()), "subnet", subnet.uuid
        )
        if external is None:
            return
        router = self.cloud._find(
            self.cloud.api.network.routers(), "router", subnet.uuid
        )
        if router:
            self.cloud.delete_router(router)
        self.cloud.delete_subnet(external)

    def delete_network(self):
        external = self.cloud._find(self.cloud.networks(), "network", self.network.uuid)
        if external:
            self.cloud.delete_network(external)

    def _port_model(self, port):
        ip = port.fixed_ips[0]
        subnet = self.cloud.api.network.get_subnet(ip["subnet_id"])

        return models.Port(
            uuid=self.cloud.identity(port, "port"),
            subnet=self.cloud.identity(subnet, "subnet"),
            project_id=self.network.project_id,
            ipv4=netaddr.IPAddress(ip["ip_address"]),
            mask=netaddr.IPNetwork(subnet.cidr).netmask,
            mac=port.mac_address,
            source=port.id,
            status="ACTIVE",
        )

    def list_ports(self, subnet):
        gateways = {endpoint.uuid for endpoint in endpoints_for_subnet(subnet.uuid)}
        return [
            self._port_model(p)
            for p in self.cloud.ports(self._network(), self._subnet(subnet.uuid))
            if self.cloud.identity(p, "port") not in gateways
        ]

    def _security_groups(self, port):
        bindings = catalog.PortSecurityGroup.objects.get_all(
            filters={"port": filters.EQ(port.uuid)}
        )
        groups = []
        for binding in bindings:
            group = catalog.SecurityGroup.objects.get_one(
                filters={"uuid": filters.EQ(binding.security_group)}
            )
            rules = []
            for rule in catalog.SecurityGroupRule.objects.get_all(
                filters={"security_group": filters.EQ(group.uuid)}
            ):
                item = {"direction": rule.direction, "ethertype": "IPv4"}
                if rule.protocol != "any":
                    item["protocol"] = rule.protocol
                if rule.port:
                    item.update(port_range_min=rule.port, port_range_max=rule.port)
                if rule.remote_ip:
                    item["remote_ip_prefix"] = rule.remote_ip
                if rule.remote_group_id:
                    item["remote_group_id"] = self.cloud.ensure_security_group(
                        rule.remote_group_id
                    ).id
                rules.append(item)
            groups.append(self.cloud.security_group(group.uuid, rules).id)
        if not groups:
            default = uuid.uuid5(self.network.uuid, "default-security")
            rules = (
                []
                if port.enforce_security_group
                else [
                    {
                        "direction": direction,
                        "ethertype": "IPv4",
                        "remote_ip_prefix": "0.0.0.0/0",
                    }
                    for direction in ("ingress", "egress")
                ]
            )
            if port.enforce_security_group:
                default = uuid.uuid5(self.network.uuid, "deny-security")
            groups.append(self.cloud.security_group(default, rules).id)
        return groups

    def create_port(self, port):
        attributes = {
            "security_group_ids": self._security_groups(port)
            if port.port_security
            else [],
            "is_port_security_enabled": port.port_security,
        }
        if port.ipv4:
            attributes["ip_address"] = str(port.ipv4)
        if port.mac:
            attributes["mac_address"] = port.mac
        external = self.cloud.ensure_port(
            port.uuid, self._subnet(port.subnet), **attributes
        )
        return self._port_model(external)

    def delete_port(self, port):
        external = self.cloud._find(
            self.cloud.ports(self._network()), "port", port.uuid
        )
        if external:
            for floating in self.cloud.api.network.ips(port_id=external.id):
                self.cloud._require(floating, "floating_ip")
                self.cloud.api.network.delete_ip(floating.id)
            self.cloud.delete_port(external)

    def update_port(self, port):
        external = self.cloud._find(
            self.cloud.ports(self._network()), "port", port.uuid
        )
        if external is None:
            return self.create_port(port)
        external = self.cloud.api.network.update_port(
            external.id,
            security_group_ids=self._security_groups(port)
            if port.port_security
            else [],
            is_port_security_enabled=port.port_security,
        )
        return self._port_model(external)

    def port_is_stale(self, port):
        external = self.cloud._find(
            self.cloud.ports(self._network()), "port", port.uuid
        )
        return external is None or set(external.security_group_ids) != set(
            self._security_groups(port)
        )
