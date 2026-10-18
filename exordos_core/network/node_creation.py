#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

"""Expand the network template during Node insertion."""

from exordos_core.common import exceptions
from exordos_core.network.contracts import resolve_subnet
from exordos_core.network.contracts import validate_inline_fields
from exordos_core.network.contracts import validate_port_input
from exordos_core.network.contracts import validate_uuid


def validate_default_network(value, template=False):
    if not isinstance(value, dict):
        raise exceptions.ValidateException(err="default_network must be an object")
    validate_inline_fields(
        value,
        {
            "network",
            "subnet",
            "auto_floating_ip",
            "floating_network",
            "target_ipv4",
            "port_security",
        },
        "default_network",
    )
    port = {
        key: item
        for key, item in value.items()
        if key not in ("auto_floating_ip", "floating_network")
    }
    validate_port_input(port)
    if "port_security" in port and not isinstance(port["port_security"], bool):
        raise exceptions.ValidateException(err="port_security must be a boolean")
    if template and "target_ipv4" in port:
        raise exceptions.ValidateException(err="NodeSet template requires automatic IP")
    if "auto_floating_ip" in value and not isinstance(value["auto_floating_ip"], bool):
        raise exceptions.ValidateException(err="auto_floating_ip must be a boolean")
    if "floating_network" in value:
        validate_uuid(value["floating_network"], "floating_network")
        if value.get("auto_floating_ip") is not True:
            raise exceptions.ValidateException(
                err="floating_network requires auto_floating_ip=true"
            )


def create_default_port(node, value, project_id, session):
    from exordos_core.compute.dm import models as compute
    from exordos_core.network.defaults import ensure_default_subnet
    from exordos_core.network.defaults import find_legacy_flat_subnet
    from exordos_core.network.defaults import resolve_default_floating_subnet
    from exordos_core.user_api.network.dm import models as catalog

    if value == {} and find_legacy_flat_subnet(project_id, session) is None:
        return None
    value = value or {}
    placement = {
        key: item
        for key, item in value.items()
        if key not in ("auto_floating_ip", "floating_network")
    }
    if (
        value.get("auto_floating_ip")
        and "network" not in value
        and "subnet" not in value
    ):
        placement["subnet"] = str(ensure_default_subnet(project_id, session).uuid)
    if "network" in placement or "subnet" in placement:
        placement["subnet"] = str(
            resolve_subnet(
                project_id,
                subnet=placement.get("subnet"),
                network=placement.get("network"),
                address=placement.get("target_ipv4"),
                session=session,
            ).uuid
        )
        placement.pop("network", None)
    port = compute.Port.restore_from_simple_view(
        **{
            **placement,
            "project_id": str(project_id),
            "node": str(node.uuid),
            "index": 0,
            "owner_node": str(node.uuid),
        }
    )
    port.insert(session=session)
    if value.get("auto_floating_ip"):
        fip = (
            {"network": value["floating_network"]}
            if "floating_network" in value
            else {
                "subnet": str(resolve_default_floating_subnet(project_id, session).uuid)
            }
        )
        address = catalog.Address.restore_from_simple_view(
            **{
                **fip,
                "kind": "floating",
                "port": str(port.uuid),
                "owner_port": str(port.uuid),
                "project_id": str(project_id),
            }
        )
        address.insert(session=session)
    return port
