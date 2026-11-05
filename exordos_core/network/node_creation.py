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
            "project_id",
            "network",
            "subnet",
            "auto_floating_ip",
            "floating_network",
            "target_ipv4",
            "port_security",
            "security_groups",
        },
        "default_network",
    )
    port = {
        key: item
        for key, item in value.items()
        if key
        not in ("project_id", "auto_floating_ip", "floating_network", "security_groups")
    }
    validate_port_input(port)
    if "project_id" in value:
        validate_uuid(value["project_id"], "default_network.project_id")
    groups = value.get("security_groups", [])
    if not isinstance(groups, list):
        raise exceptions.ValidateException(err="security_groups must be a list")
    for group in groups:
        validate_uuid(group, "security_groups")
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


def select_subnet(value, project_id, session, *, allow_foreign=False):
    from exordos_core.network.defaults import ensure_default_subnet
    from exordos_core.network.defaults import find_legacy_flat_subnet

    value = value or {}
    placement_project = value.get("project_id", project_id)
    if str(placement_project) != str(project_id) and not allow_foreign:
        raise exceptions.ValidateException(
            err="default network belongs to another project"
        )
    if value.get("subnet") or value.get("network"):
        return resolve_subnet(
            placement_project,
            subnet=value.get("subnet"),
            network=value.get("network"),
            address=value.get("target_ipv4"),
            session=session,
            allow_foreign=allow_foreign and str(placement_project) == str(project_id),
        )
    if value.get("auto_floating_ip"):
        return ensure_default_subnet(placement_project, session)
    legacy = find_legacy_flat_subnet(placement_project, session)
    if legacy is not None:
        return legacy
    return ensure_default_subnet(placement_project, session)


def create_default_port(node, value, project_id, session, *, allow_foreign=False):
    from exordos_core.compute.dm import models as compute
    from exordos_core.network.defaults import find_legacy_flat_subnet
    from exordos_core.network.defaults import resolve_default_floating_subnet
    from exordos_core.network.routed import requested_network
    from exordos_core.user_api.network.dm import models as catalog

    if (
        requested_network(value, project_id, session, allow_foreign=allow_foreign)
        is not None
    ):
        for group in (value or {}).get("security_groups", []):
            catalog.validate_reference(
                project_id,
                catalog.SecurityGroup,
                group,
                "security_group",
                session=session,
            )
        return None

    parent = None
    if node.node_set is not None:
        parent = compute.NodeSet.objects.get_one(
            filters={"uuid": node.node_set},
            session=session,
        )
        parent_project = (parent.default_network or {}).get(
            "project_id", parent.project_id
        )
        if str(parent.project_id) != str(project_id) and not (
            allow_foreign and str(parent_project) == str(project_id)
        ):
            raise exceptions.ValidateException(err="NodeSet belongs to another project")
    if (
        value == {}
        and (parent is None or parent.network_subnet is None)
        and find_legacy_flat_subnet(project_id, session) is None
    ):
        return None
    value = value or {}
    placement = {
        key: item
        for key, item in value.items()
        if key
        not in ("project_id", "auto_floating_ip", "floating_network", "security_groups")
    }
    if parent is not None and parent.network_subnet is not None:
        subnet = compute.Subnet.objects.get_one(
            filters={"uuid": parent.network_subnet},
            session=session,
        )
        allow_foreign = True
    else:
        subnet = select_subnet(value, project_id, session, allow_foreign=allow_foreign)
    placement["subnet"] = str(subnet.uuid)
    placement.pop("network", None)
    port = compute.Port.restore_from_simple_view(
        **{
            **placement,
            "project_id": str(project_id),
            "subnet": str(subnet.uuid),
            "node": str(node.uuid),
            "index": 0,
            "owner_node": str(node.uuid),
        }
    )
    port._allow_foreign_subnet = allow_foreign
    groups = (
        (parent.default_network or {}).get("security_groups", [])
        if parent
        else value.get("security_groups", [])
    )
    network = compute.Network.objects.get_one(
        filters={"uuid": subnet.network}, session=session
    )
    managed_policy = network.driver_spec.get("driver") in {"ovs_evpn", "cloud"}
    if groups and managed_policy:
        port.enforce_security_group = True
    port.insert(session=session)
    if groups and managed_policy:
        for group in sorted(set(groups)):
            catalog.PortSecurityGroup(
                project_id=project_id,
                port=port.uuid,
                security_group=validate_uuid(group, "security_group"),
            ).insert(session=session)
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
