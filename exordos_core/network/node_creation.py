#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

"""Expand a create-only request into canonical network resources."""

from exordos_core.common import exceptions
from exordos_core.network.contracts import INLINE_FLOATING_CREATE_FIELDS
from exordos_core.network.contracts import validate_address_input
from exordos_core.network.contracts import validate_inline_fields
from exordos_core.network.contracts import validate_port_input


def validate_default_network(value, template=False):
    if not isinstance(value, dict):
        raise exceptions.ValidateException(err="default_network must be an object")
    validate_inline_fields(
        value,
        {"network", "subnet", "floating_ip", "target_ipv4", "port_security"},
        "default_network",
    )
    port = {key: item for key, item in value.items() if key != "floating_ip"}
    validate_port_input(port)
    if "port_security" in port and not isinstance(port["port_security"], bool):
        raise exceptions.ValidateException(err="port_security must be a boolean")
    if template and "target_ipv4" in port:
        raise exceptions.ValidateException(err="NodeSet template requires automatic IP")
    if "floating_ip" in value:
        fip = value["floating_ip"]
        if fip is not True:
            if not isinstance(fip, dict):
                raise exceptions.ValidateException(
                    err="floating_ip must be true or a create object"
                )
            validate_inline_fields(fip, INLINE_FLOATING_CREATE_FIELDS, "FloatingCreate")
            validate_address_input({**fip, "kind": "floating"})
            if template and "address" in fip:
                raise exceptions.ValidateException(
                    err="NodeSet template requires automatic FIP"
                )


def create_default_port(node, value, project_id, session):
    from exordos_core.compute import constants
    from exordos_core.compute.dm import models as compute
    from exordos_core.network.defaults import ensure_default_subnet
    from exordos_core.network.defaults import find_legacy_flat_subnet
    from exordos_core.network.defaults import resolve_default_floating_subnet
    from exordos_core.user_api.network.dm import models as catalog

    if value == {} and find_legacy_flat_subnet(project_id, session) is None:
        return None
    value = value or {}
    placement = {key: item for key, item in value.items() if key != "floating_ip"}
    if "floating_ip" in value and "network" not in value and "subnet" not in value:
        placement["subnet"] = str(ensure_default_subnet(project_id, session).uuid)
    port = compute.Port.restore_from_simple_view(
        **{
            **placement,
            "project_id": str(project_id),
            "node": str(node.uuid),
            "index": 0,
            **(
                {"provenance": constants.PortProvenance.GENERATED.value}
                if node.node_set is not None
                and node.project_id == constants.NODE_SET_PROJECT
                else {}
            ),
        }
    )
    port.insert(session=session)
    if "floating_ip" in value:
        fip = value["floating_ip"]
        if fip is True:
            fip = {
                "subnet": str(resolve_default_floating_subnet(project_id, session).uuid)
            }
        address = catalog.Address.restore_from_simple_view(
            **{
                **fip,
                "kind": "floating",
                "port": str(port.uuid),
                "project_id": str(project_id),
            }
        )
        address.insert(session=session)
    return port
