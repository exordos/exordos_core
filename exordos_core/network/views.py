# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0.
"""Explicit, batched observation loading for API and agent responses."""

from gcl_sdk.agents.universal.dm import models as ua
from restalchemy.dm import filters

from exordos_core.compute.dm import models as compute
from exordos_core.network.contracts import applied_by_owner
from exordos_core.user_api.network.dm import models as catalog


def _rows(model, field, values, session=None, **kwargs):
    values = {
        str(getattr(value, "uuid", value)) for value in values if value is not None
    }
    clauses = {field: filters.In(sorted(values)), **kwargs.pop("filters", {})}
    return (
        model.objects.get_all(filters=clauses, session=session, **kwargs)
        if values
        else []
    )


def _networks(rows, session):
    return {
        subnet.uuid: getattr(subnet.network, "uuid", subnet.network)
        for subnet in _rows(compute.Subnet, "uuid", (r.subnet for r in rows), session)
    }


def port_security_groups(ports, session=None):
    groups = {port.uuid: [] for port in ports}
    for link in _rows(catalog.PortSecurityGroup, "port", groups, session):
        groups[link.port].append(link.security_group)
    return groups


def security_group_rules(groups, session=None):
    rules = {group: [] for group in groups}
    for rule in _rows(
        catalog.SecurityGroupRule,
        "security_group",
        rules,
        session,
        order_by={"uuid": "asc"},
    ):
        rules[rule.security_group].append(rule.rule_view())
    return rules


def project_ports(ports, session=None):
    ports = list(ports)
    networks = _networks(ports, session)
    memberships = port_security_groups(ports, session)
    for port in ports:
        port.network = networks.get(getattr(port.subnet, "uuid", port.subnet))
        port.security_groups = memberships[port.uuid]
    return ports


def project_security_groups(groups, session=None):
    groups = list(groups)
    rules = security_group_rules((group.uuid for group in groups), session)
    for group in groups:
        group.rules = rules[group.uuid]
    return groups


def address_status(address, target=None, actual=None, agents=None):
    if address.kind == "reservation":
        return "ACTIVE"
    if address.port is None:
        return "IN_PROGRESS" if address.association is not None else "UNBOUND"
    current = applied_by_owner(target, actual, agents=agents or {})
    if (
        current
        and actual.status == "ACTIVE"
        and any(
            str(f.get("public")) == str(address.address)
            for f in actual.value.get("fips", [])
        )
    ):
        return "ACTIVE"
    return "ERROR" if current and actual.status == "ERROR" else "IN_PROGRESS"


def project_addresses(addresses, session=None):
    addresses = list(addresses)
    networks = _networks(addresses, session)
    ports = {a.port for a in addresses if a.kind == "floating" and a.port is not None}
    observations = []
    for model in (ua.TargetResource, ua.Resource):
        observations.append(
            {
                row.uuid: row
                for row in _rows(
                    model,
                    "uuid",
                    ports,
                    session,
                    # Several resource kinds can share a uuid.
                    filters={"kind": filters.EQ("evpn_port")},
                )
            }
        )
    targets, actuals = observations
    agents = {
        agent.uuid: agent
        for agent in _rows(
            ua.UniversalAgent,
            "uuid",
            (
                target.agent
                for target in targets.values()
                if target.uuid in actuals and actuals[target.uuid].hash == target.hash
            ),
            session,
        )
    }
    for address in addresses:
        address.network = networks.get(address.subnet)
        address.status = address_status(
            address, targets.get(address.port), actuals.get(address.port), agents
        )
    return addresses


def project_nodes(nodes, session=None):
    nodes = list(nodes)
    ports = {
        port.node: port
        for port in _rows(
            compute.Port,
            "node",
            (node.uuid for node in nodes),
            session,
            filters={"index": filters.EQ(0)},
        )
    }
    networks = _networks(ports.values(), session)
    floating = {
        address.port: address
        for address in _rows(
            catalog.Address,
            "port",
            (port.uuid for port in ports.values()),
            session,
            filters={"kind": filters.EQ("floating")},
        )
    }
    for node in nodes:
        request = node.properties.get_value("default_network")
        view = dict(request or {})
        port = ports.get(node.uuid)
        if port is not None:
            network = networks.get(getattr(port.subnet, "uuid", port.subnet))
            view = {
                "network": str(network) if network is not None else None,
                "subnet": str(getattr(port.subnet, "uuid", port.subnet)),
                **view,
                "port": str(port.uuid),
                "ipv4": str(port.ipv4) if port.ipv4 else None,
                "mask": str(port.mask) if port.mask else None,
                "mac": port.mac,
            }
            if address := floating.get(port.uuid):
                view["floating_ip"] = str(address.address)
        node._default_network_view = (dict(request) if request else request, view)
    return nodes
