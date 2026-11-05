# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0

"""Manage an SDN-side link gateway through ordinary Core compute resources."""

import uuid

from gcl_sdk.agents.universal.dm import models as ua
from restalchemy.dm import filters

from exordos_core.compute.dm import models as compute
from exordos_core.config.dm import models as config
from exordos_core.elements.dm import models as elements
from exordos_core.network.contracts import atomic_session

AGENT_NAME = "cloud-net-link"


def managed(endpoint):
    return endpoint.connection is None and "external_subnet" in endpoint.definition


def require_unmanaged(node):
    from restalchemy.storage import exceptions

    from exordos_core.cloud.links import CloudLinkEndpoint

    if node is None:
        return
    endpoint = CloudLinkEndpoint.objects.get_one_or_none(
        filters={"uuid": filters.EQ(getattr(node, "uuid", node))}
    )
    if endpoint is not None and managed(endpoint):
        raise exceptions.ConflictRecords(
            model="Node", msg="owned by a cloud link; change the link instead"
        )


def reserve(link, endpoint):
    definition = endpoint.definition
    with atomic_session(compute.Node._get_engine(), None) as session:
        node = compute.Node.objects.get_one_or_none(
            filters={"uuid": filters.EQ(endpoint.uuid)}, session=session
        )
        if node is None:
            node = compute.Node.restore_from_simple_view(
                uuid=str(endpoint.uuid),
                project_id=str(link.project_id),
                name="cloud-link-" + endpoint.uuid.hex[:8],
                cores=definition["gateway_cores"],
                ram=definition["gateway_ram"],
                disk_spec={
                    "kind": "root_disk",
                    "size": definition["gateway_disk"],
                    "image": definition["gateway_image"],
                    "ephemeral": True,
                },
                default_network={
                    "subnet": str(endpoint.subnet),
                    "port_security": False,
                },
            )
            # Both gateway ports are reserved below, including managed subnets.
            node._insert_node(session=session)
        ports = compute.Port.objects.get_all(
            filters={"node": filters.EQ(endpoint.uuid)}, session=session
        )
        previous = link.state.get(str(endpoint.uuid), {})
        for index, subnet, suffix, security, address in [
            (0, endpoint.subnet, "private-port", False, previous.get("gateway_ip")),
            (
                1,
                uuid.UUID(definition["external_subnet"]),
                "external-port",
                True,
                previous.get("endpoint"),
            ),
        ]:
            if not any(port.index == index for port in ports):
                compute.Port.restore_from_simple_view(
                    uuid=str(uuid.uuid5(endpoint.uuid, suffix)),
                    project_id=str(link.project_id),
                    subnet=str(subnet),
                    node=str(endpoint.uuid),
                    owner_node=str(endpoint.uuid),
                    index=index,
                    port_security=security,
                    target_ipv4=address,
                ).insert(session=session)
    agent = uuid.uuid5(endpoint.uuid, AGENT_NAME)
    if (
        ua.UniversalAgent.objects.get_one_or_none(filters={"uuid": filters.EQ(agent)})
        is None
    ):
        ua.UniversalAgent(
            uuid=agent,
            node=endpoint.uuid,
            name=AGENT_NAME,
            capabilities={"capabilities": ["cloud_net_link"]},
            facts={"facts": []},
        ).insert()
    filename = "/etc/exordos_universal_agent/cloud-link.conf"
    identifier = uuid.uuid5(endpoint.uuid, "agent-config")
    if (
        config.Config.objects.get_one_or_none(filters={"uuid": filters.EQ(identifier)})
        is None
    ):
        config.Config.restore_from_simple_view(
            uuid=str(identifier),
            project_id=str(link.project_id),
            path=filename,
            mode="0600",
            target={"kind": "node", "node": str(endpoint.uuid)},
            body={
                "kind": "text",
                "content": "[universal_agent]\nuuid5_name = cloud-net-link\ncaps_drivers = OpenVPNCapabilityDriver\npayload_path = /var/lib/exordos/universal_agent/cloud-link-payload.json\n",
            },
        ).insert()
    identifier = uuid.uuid5(endpoint.uuid, "agent-service")
    if (
        elements.Service.objects.get_one_or_none(
            filters={"uuid": filters.EQ(identifier)}
        )
        is None
    ):
        elements.Service.restore_from_simple_view(
            uuid=str(identifier),
            project_id=str(link.project_id),
            name=AGENT_NAME,
            path="/usr/bin/exordos-universal-agent --config-file /etc/exordos_universal_agent/exordos_universal_agent.conf --config-file "
            + filename,
            target={"kind": "node", "node": str(endpoint.uuid)},
            service_type={"kind": "simple"},
            before=[
                {
                    "kind": "shell",
                    "command": "test -s "
                    + filename
                    + " && (command -v openvpn >/dev/null || (apt-get update && apt-get install -y openvpn iptables))",
                }
            ],
        ).insert()
    ports = compute.Port.objects.get_all(filters={"node": filters.EQ(endpoint.uuid)})
    addresses = {port.index: port.ipv4 for port in ports}
    if not addresses.get(0) or not addresses.get(1):
        raise ValueError("Local gateway ports are not ready")
    return {
        "agent": str(agent),
        "endpoint": str(addresses[1]),
        "gateway_ip": str(addresses[0]),
        "node_id": str(node.uuid),
    }


def booted(endpoint):
    node = compute.Node.objects.get_one_or_none(
        filters={"uuid": filters.EQ(endpoint.uuid)}
    )
    return node is not None and node.status == "ACTIVE"


def fence(endpoint):
    node = compute.Node.objects.get_one_or_none(
        filters={"uuid": filters.EQ(endpoint.uuid)}
    )
    if node:
        node.delete()


def remove(endpoint):
    for model, suffix in [
        (elements.Service, "agent-service"),
        (config.Config, "agent-config"),
    ]:
        resource = model.objects.get_one_or_none(
            filters={"uuid": filters.EQ(uuid.uuid5(endpoint.uuid, suffix))}
        )
        if resource:
            resource.delete()
    fence(endpoint)
    # Core's normal builders own machine, disk and port teardown.
    if compute.Machine.objects.get_all(filters={"node": filters.EQ(endpoint.uuid)}):
        return False
    return True
