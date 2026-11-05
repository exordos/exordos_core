# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0

"""Provision link gateways through a remote Core's ordinary resources."""

import ipaddress
import uuid

from gcl_sdk.agents.universal.dm import models as ua
from gcl_sdk.agents.universal.drivers import pool
from gcl_sdk.agents.universal.drivers.core_pool import CorePoolDriver
from gcl_sdk.cloud import bootstrap
from gcl_sdk.cloud import openvpn
from restalchemy.dm import filters

from exordos_core.cloud.bootstrap import agent_config
from exordos_core.compute.dm import models as compute
from exordos_core.user_api.network.dm import models as catalog


def gateway_pool(cloud):
    return uuid.uuid5(cloud.config.connection, "network-gateways")


def gateways(cloud, endpoint):
    result = []
    for node in cloud.list("node"):
        identity = cloud.identity("node", node)
        config = cloud.get("config", identity)
        if config is None:
            raise ValueError("Remote gateway bootstrap is missing")
        metadata = cloud.metadata(config)["machine"]
        if metadata.get("pool") == str(gateway_pool(cloud)) and metadata.get(
            "node"
        ) == str(endpoint.uuid):
            result.append((identity, node))
    return result


def transport_port(endpoint):
    return uuid.uuid5(endpoint.uuid, "transport-port")


def reserve(cloud, link, endpoint):
    subnet = cloud.get("subnet", endpoint.subnet)
    if subnet is None or subnet["status"] != "ACTIVE":
        raise ValueError("Remote gateway subnet is not ready")
    external = cloud.client.get(
        cloud.path("subnet"), uuid.UUID(endpoint.definition["external_subnet"])
    )
    if str(external["project_id"]) != cloud.project:
        raise ValueError("Remote transport subnet belongs to another project")
    private = cloud.ensure(
        "port",
        endpoint.uuid,
        name="gateway-private",
        subnet=subnet["uuid"],
        port_security=False,
    )
    transport_network = cloud.client.get(
        cloud.path("network"), uuid.UUID(external["network"])
    )
    filtered = transport_network["driver_spec"].get("driver") in {
        "cloud",
        "ovs_evpn",
        "routed",
    }
    transport = cloud.ensure(
        "port",
        transport_port(endpoint),
        name="gateway-transport",
        subnet=external["uuid"],
        enforce_security_group=filtered,
    )
    if filtered:
        group = cloud.ensure("security_group", endpoint.uuid, name="gateway-transport")
        rules = [{"direction": "egress", "protocol": "any", "remote_ip": "0.0.0.0/0"}]
        if endpoint.definition.get("role") != "client":
            rules.append(
                {
                    "direction": "ingress",
                    "protocol": link.transport["protocol"],
                    "port": link.transport["port"],
                    "remote_ip": "0.0.0.0/0",
                }
            )
        for index, rule in enumerate(rules):
            cloud.ensure(
                "security_group_rule",
                uuid.uuid5(endpoint.uuid, "transport-rule:" + str(index)),
                security_group=group["uuid"],
                **rule,
            )
        cloud.ensure(
            "port_security_group",
            endpoint.uuid,
            port=transport["uuid"],
            security_group=group["uuid"],
        )
    if not private.get("ipv4") or not transport.get("ipv4"):
        raise ValueError("Remote gateway addresses are not allocated")
    recovery = link.recovery.get(str(endpoint.uuid))
    current = gateways(cloud, endpoint)
    if len(current) > 1:
        raise ValueError("Multiple remote gateways claim the endpoint")
    for identity, node in current:
        if recovery == node["uuid"]:
            for port in (endpoint.uuid, transport_port(endpoint)):
                cloud.update("port", port, node=None, owner_node=None, index=None)
            cloud.delete("node", identity)
            current = []
    resource = (
        current[0][0]
        if current
        else uuid.uuid5(endpoint.uuid, "gateway:" + (recovery or "initial"))
    )
    reservation = catalog.Address.objects.get_one_or_none(
        filters={"uuid": filters.EQ(endpoint.uuid)}
    )
    if reservation is not None and str(reservation.address) != private["ipv4"]:
        raise ValueError("Remote gateway reservation changed")
    if reservation is None:
        catalog.Address.restore_from_simple_view(
            uuid=str(endpoint.uuid),
            project_id=str(link.project_id),
            subnet=str(endpoint.subnet),
            kind="reservation",
            address=private["ipv4"],
        ).insert()
    return {
        "endpoint": transport["ipv4"]
        if endpoint.definition.get("role") != "client"
        else None,
        "gateway_ip": private["ipv4"],
        "port_id": private["uuid"],
        "agent": str(endpoint.uuid),
        "resource": str(resource),
    }


def routes(cloud, endpoint, state, remote_cidrs):
    from exordos_core.network.subnet import effective_routers

    subnet = cloud.get("subnet", endpoint.subnet)
    if not remote_cidrs and subnet["status"] == "DELETING":
        return
    old = subnet.get("routers")
    if old is None:
        local = compute.Subnet.objects.get_one(
            filters={"uuid": filters.EQ(endpoint.subnet)}
        )
        old = [
            {"to": str(r["to"]), "via": str(r["via"])} for r in effective_routers(local)
        ]
    gateway = state["gateway_ip"]
    target = [r for r in old if r["via"] != gateway]
    if any(
        ipaddress.IPv4Network(route["to"]).overlaps(ipaddress.IPv4Network(cidr))
        for route in target
        if route["to"] != "0.0.0.0/0"
        for cidr in remote_cidrs
    ):
        raise ValueError("An existing route conflicts with the cloud link")
    target.extend({"to": cidr, "via": gateway} for cidr in remote_cidrs)
    if target != old:
        cloud.update("subnet", endpoint.subnet, routers=target)


def boot(cloud, link, endpoint, state, bundle):
    kind = "cloud_net_link"
    if cloud.get("node", uuid.UUID(state["resource"])) is None:
        for actual in ua.Resource.objects.get_all(
            filters={"uuid": filters.EQ(endpoint.uuid), "kind": filters.EQ(kind)}
        ):
            actual.delete()
    if (
        ua.UniversalAgent.objects.get_one_or_none(
            filters={"uuid": filters.EQ(endpoint.uuid)}
        )
        is None
    ):
        ua.UniversalAgent(
            uuid=endpoint.uuid,
            node=endpoint.uuid,
            name="cloud-link-gateway",
            capabilities={"capabilities": [kind]},
            facts={"facts": []},
        ).insert()
    key = ua.NodeEncryptionKey.get_or_create(endpoint.uuid)
    user_data = bootstrap.agent_user_data(
        endpoint.uuid,
        key.private_key,
        *agent_config(),
        hostname="cloud-link-" + endpoint.uuid.hex[:8],
        caps_drivers="OpenVPNCapabilityDriver",
    )
    machine = pool.Machine(
        uuid=uuid.UUID(state["resource"]),
        node=endpoint.uuid,
        project_id=link.project_id,
        pool=gateway_pool(cloud),
        name="cloud-link-" + endpoint.uuid.hex[:8],
        cores=endpoint.definition["gateway_cores"],
        ram=endpoint.definition["gateway_ram"],
        image=endpoint.definition["gateway_image"],
        boot="hd0",
        user_data=openvpn.bootstrap_user_data(endpoint.uuid, bundle, user_data),
    )
    volume = pool.MachineVolume(
        uuid=uuid.uuid5(machine.uuid, "root-volume"),
        project_id=link.project_id,
        machine=machine.uuid,
        name="root",
        index=0,
        size=endpoint.definition.get("gateway_disk", 8),
        image=machine.image,
    )
    ports = []
    for identity in (endpoint.uuid, transport_port(endpoint)):
        port = cloud.get("port", identity)
        ports.append(
            pool.Port(
                uuid=identity,
                project_id=link.project_id,
                mac=port["mac"],
                source=port["uuid"],
            )
        )
    adapter = CorePoolDriver(
        pool.MachinePool(
            uuid=gateway_pool(cloud),
            driver_spec=pool.CloudPoolDriverSpec(connection=cloud.config.connection),
        ),
        cloud,
    )
    actual, _ = adapter.create_machine(machine, [volume], ports)
    node = cloud.get("node", machine.uuid)
    state["server_id"] = node["uuid"]
    recovery = link.recovery.get(str(endpoint.uuid))
    if recovery and node["uuid"] != recovery:
        link.complete_recovery(endpoint.uuid, recovery)
    return actual.status == "ACTIVE"


def fence(cloud, endpoint):
    for identity, _ in gateways(cloud, endpoint):
        cloud.delete("node", identity)


def remove(cloud, link, endpoint):
    current = gateways(cloud, endpoint)
    if current:
        fence(cloud, endpoint)
        return False
    state = link.state.get(str(endpoint.uuid), {})
    if state.get("gateway_ip") and cloud.get("subnet", endpoint.subnet):
        routes(cloud, endpoint, state, [])
    for identity in (endpoint.uuid, transport_port(endpoint)):
        cloud.delete("port", identity)
    cloud.delete("port_security_group", endpoint.uuid)
    for index in (0, 1):
        cloud.delete(
            "security_group_rule",
            uuid.uuid5(endpoint.uuid, "transport-rule:" + str(index)),
        )
    cloud.delete("security_group", endpoint.uuid)
    for model in (catalog.Address, ua.UniversalAgent, ua.NodeEncryptionKey):
        for obj in model.objects.get_all(filters={"uuid": filters.EQ(endpoint.uuid)}):
            obj.delete()
    for obj in ua.Resource.objects.get_all(
        filters={
            "uuid": filters.EQ(endpoint.uuid),
            "kind": filters.EQ("cloud_net_link"),
        }
    ):
        obj.delete()
    endpoint.delete()
    return True
