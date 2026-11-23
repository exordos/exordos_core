# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0

import contextlib
import uuid

import netaddr
from restalchemy.dm import filters

from exordos_core.cloud.locking import provider_lock
from exordos_core.cloud.models import connection
from exordos_core.compute.dm import models
from exordos_core.network.driver import base
from exordos_core.user_api.network.dm import models as catalog


class CoreNetworkDriver(base.AbstractNetworkDriver):
    def __init__(self, network):
        if network.driver_spec.get("driver") != "cloud":
            raise ValueError("Not a cloud network")
        self.bind(network)

    def bind(self, network):
        self.network = network
        config = connection(network.driver_spec["connection"], network.project_id)
        if config.driver_spec.KIND != "core":
            raise ValueError("Not a remote Core network")
        self.cloud = config.load_driver()

    def _network(self):
        root = self.network
        if root.owner_network:
            root = models.Network.objects.get_one(
                filters={"uuid": filters.EQ(root.owner_network)}
            )
        return self.cloud.ensure(
            "network", self.network.uuid, name=self.network.name, cidr=str(root.cidr)
        )

    def _retiring(self):
        if self.network.owner_network is None:
            return False
        from exordos_core.network.routed import owner

        segment = owner(network=self.network.uuid)
        return segment is None or segment.status == "DELETING"

    @contextlib.contextmanager
    def reconcile(self):
        with provider_lock(self.cloud.config.connection):
            current = models.Network.objects.get_one(
                filters={"uuid": filters.EQ(self.network.uuid)}
            )
            self.bind(current)
            if not self._retiring():
                self._network()
            yield

    def _subnet(self, resource):
        external = self.cloud.get("subnet", resource)
        if external is None:
            raise ValueError("Remote subnet is not ready")
        return external

    def list_subnets(self):
        network = self.cloud.get("network", self.network.uuid)
        if network is None:
            return []
        return [
            models.Subnet(
                uuid=self.cloud.identity("subnet", s),
                network=self.network.uuid,
                project_id=self.network.project_id,
                cidr=netaddr.IPNetwork(s["cidr"]),
                dhcp=s["dhcp"],
            )
            for s in self.cloud.list("subnet", network=network["uuid"])
            if s["status"] == "ACTIVE"
        ]

    def create_subnet(self, subnet):
        if subnet.status == "DELETING" or self._retiring():
            return subnet
        self.cloud.ensure(
            "subnet",
            subnet.uuid,
            name=subnet.name or "provider-subnet",
            network=self._network()["uuid"],
            cidr=str(subnet.cidr),
            hypervisors=[str(ref) for ref in self.cloud.config.hypervisors],
            dhcp=subnet.dhcp,
            dns_servers=subnet.dns_servers or [],
        )
        return subnet

    def update_subnet(self, subnet):
        if subnet.status == "DELETING" or self._retiring():
            return subnet
        self.cloud.update(
            "subnet",
            subnet.uuid,
            dhcp=subnet.dhcp,
            dns_servers=subnet.dns_servers or [],
        )
        return subnet

    def refresh_generated(self, subnet):
        self.update_subnet(subnet)

    def native_ready(self, subnet, router):
        return self._subnet(subnet)["status"] == "ACTIVE"

    def delete_subnet(self, subnet):
        self.cloud.delete("subnet", subnet.uuid)
        if self.cloud.get("subnet", subnet.uuid) is not None:
            raise ValueError("Remote subnet cleanup is in progress")

    def delete_network(self):
        self.cloud.delete("network", self.network.uuid)
        groups = [
            g
            for g in self.cloud.list("security_group")
            if g["name"].startswith(self._group_prefix())
        ]
        for group in groups:
            for rule in self.cloud.client.filter(
                self.cloud.path("security_group_rule"),
                project_id=self.cloud.project,
                security_group=group["uuid"],
            ):
                self.cloud.client.delete(
                    self.cloud.path("security_group_rule"), uuid.UUID(rule["uuid"])
                )
        for group in groups:
            self.cloud.delete(
                "security_group", self.cloud.identity("security_group", group)
            )
        if self.cloud.get("network", self.network.uuid) is not None:
            raise ValueError("Remote network cleanup is in progress")

    def _port_model(self, port):
        subnet = self.cloud.client.get(
            self.cloud.path("subnet"), uuid.UUID(port["subnet"])
        )
        cidr = netaddr.IPNetwork(subnet["cidr"])
        return models.Port(
            uuid=self.cloud.identity("port", port),
            subnet=self.cloud.identity("subnet", subnet),
            project_id=self.network.project_id,
            ipv4=netaddr.IPAddress(port["ipv4"]) if port.get("ipv4") else None,
            mask=cidr.netmask,
            mac=port.get("mac"),
            source=port["uuid"],
            status="ACTIVE" if port.get("ipv4") and port.get("mac") else "IN_PROGRESS",
        )

    def list_ports(self, subnet):
        from exordos_core.cloud.links import endpoints_for_subnet

        gateways = {endpoint.uuid for endpoint in endpoints_for_subnet(subnet.uuid)}
        external = self.cloud.get("subnet", subnet.uuid)
        return (
            []
            if external is None
            else [
                self._port_model(p)
                for p in self.cloud.list("port", subnet=external["uuid"])
                if self.cloud.identity("port", p) not in gateways
            ]
        )

    def _security_group_id(self, group):
        return uuid.uuid5(self.network.uuid, "security-group:" + str(group))

    def _group_prefix(self):
        return "provider:" + str(self.network.uuid) + ":"

    def _security_groups(self, port):
        result = []
        for binding in catalog.PortSecurityGroup.objects.get_all(
            filters={"port": filters.EQ(port.uuid)}
        ):
            group = catalog.SecurityGroup.objects.get_one(
                filters={"uuid": filters.EQ(binding.security_group)}
            )
            identity = self._security_group_id(group.uuid)
            remote = self.cloud.ensure(
                "security_group", identity, name=self._group_prefix() + str(identity)
            )
            source = [
                r.dump_to_simple_view()
                for r in catalog.SecurityGroupRule.objects.get_all(
                    filters={"security_group": filters.EQ(group.uuid)}
                )
            ]
            if self.network.owner_network is not None:
                from exordos_core.network.routed import expand_security_rules

                source = expand_security_rules(port, source)
            desired = set()
            for rule in source:
                fields = {
                    k: rule[k]
                    for k in ("direction", "protocol", "port", "remote_ip")
                    if rule.get(k) is not None
                }
                if rule.get("remote_group_id"):
                    ref = self._security_group_id(rule["remote_group_id"])
                    fields["remote_group_id"] = self.cloud.ensure(
                        "security_group", ref, name=self._group_prefix() + str(ref)
                    )["uuid"]
                key = uuid.uuid5(identity, json_rule(fields))
                desired.add(str(self.cloud.resource_id("security_group_rule", key)))
                self.cloud.ensure(
                    "security_group_rule",
                    key,
                    name="provider-rule",
                    security_group=remote["uuid"],
                    **fields,
                )
            for old in self.cloud.client.filter(
                self.cloud.path("security_group_rule"),
                project_id=self.cloud.project,
                security_group=remote["uuid"],
            ):
                if old["uuid"] not in desired:
                    self.cloud.client.delete(
                        self.cloud.path("security_group_rule"), uuid.UUID(old["uuid"])
                    )
            result.append(remote["uuid"])
        return sorted(result)

    def create_port(self, port):
        addresses = {}
        if port.ipv4 or port.target_ipv4:
            addresses["target_ipv4"] = str(port.ipv4 or port.target_ipv4)
        if port.mac or port.target_mac:
            addresses["target_mac"] = port.mac or port.target_mac
        remote = self.cloud.ensure(
            "port",
            port.uuid,
            name=port.name or "provider-port",
            subnet=self._subnet(port.subnet)["uuid"],
            port_security=port.port_security,
            enforce_security_group=port.enforce_security_group,
            **addresses,
        )
        self._bindings(port, remote)
        return self._port_model(remote)

    def update_port(self, port):
        if self.cloud.get("port", port.uuid) is None:
            return self.create_port(port)
        remote = self.cloud.update(
            "port",
            port.uuid,
            port_security=port.port_security,
            enforce_security_group=port.enforce_security_group,
        )
        self._bindings(port, remote)
        return self._port_model(remote)

    def _bindings(self, port, remote):
        groups = self._security_groups(port)
        for group in groups:
            key = uuid.uuid5(port.uuid, "group:" + group)
            self.cloud.ensure(
                "port_security_group", key, port=remote["uuid"], security_group=group
            )
        for binding in self.cloud.client.filter(
            self.cloud.path("port_security_group"),
            project_id=self.cloud.project,
            port=remote["uuid"],
        ):
            if binding["security_group"] not in groups:
                self.cloud.client.delete(
                    self.cloud.path("port_security_group"), uuid.UUID(binding["uuid"])
                )

    def port_is_stale(self, port):
        remote = self.cloud.get("port", port.uuid)
        if remote is None:
            return True
        groups = self._security_groups(port)
        return (
            sorted(remote.get("security_groups", [])) != groups
            or remote["port_security"] != port.port_security
            or remote["enforce_security_group"] != port.enforce_security_group
        )

    def delete_port(self, port):
        self.cloud.delete("port", port.uuid)


def json_rule(value):
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"))
