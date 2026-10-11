#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

"""Expand a create-only request into canonical network resources."""

import uuid

from restalchemy.dm import filters
from restalchemy.storage import exceptions as storage_exc

from exordos_core.common import exceptions
from exordos_core.network.contracts import INLINE_FLOATING_CREATE_FIELDS
from exordos_core.network.contracts import INLINE_PORT_CREATE_FIELDS
from exordos_core.network.contracts import validate_address_input
from exordos_core.network.contracts import validate_inline_fields
from exordos_core.network.contracts import validate_port_input
from exordos_core.network.contracts import validate_uuid


def validate_networking(value, template=False):
    if not isinstance(value, list):
        raise exceptions.ValidateException(err="networking must be an array")
    indexes, ports = set(), set()
    for entry in value:
        if (
            not isinstance(entry, dict)
            or "port" not in entry
            or set(entry)
            - {"port", "index", "security_groups", "identity_groups", "floating_ip"}
        ):
            raise exceptions.ValidateException(
                err="networking entry requires port and accepts index, security_groups, identity_groups, floating_ip"
            )
        if "index" in entry:
            index = entry["index"]
            if not isinstance(index, int) or isinstance(index, bool) or index < 0:
                raise exceptions.ValidateException(
                    err="networking index must be a non-negative integer"
                )
            if index in indexes:
                raise storage_exc.ConflictRecords(
                    model="Port", msg="duplicate networking index"
                )
            indexes.add(index)
        port = entry["port"]
        if not isinstance(port, (str, dict)):
            raise exceptions.ValidateException(
                err="port must be a UUID or create object"
            )
        if isinstance(port, str):
            validate_uuid(port, "port")
            if port in ports:
                raise storage_exc.ConflictRecords(
                    model="Port", msg="duplicate networking port"
                )
            ports.add(port)
        if isinstance(port, dict):
            validate_inline_fields(
                port,
                INLINE_PORT_CREATE_FIELDS,
                "PortCreate",
            )
            validate_port_input(port)
        for key in ("security_groups", "identity_groups"):
            if key in entry:
                if not isinstance(entry[key], list):
                    raise exceptions.ValidateException(
                        err=f"{key} must be a UUID array"
                    )
                for group in entry[key]:
                    validate_uuid(group, key)
        if "floating_ip" in entry and not isinstance(entry["floating_ip"], (str, dict)):
            raise exceptions.ValidateException(
                err="floating_ip must be a UUID or create object"
            )
        fip = entry.get("floating_ip")
        if isinstance(fip, str):
            validate_uuid(fip, "floating_ip")
        elif isinstance(fip, dict):
            validate_inline_fields(
                fip,
                INLINE_FLOATING_CREATE_FIELDS,
                "FloatingCreate",
            )
            validate_address_input({**fip, "kind": "floating"})
        if template:
            if (
                isinstance(port, str)
                or port.get("target_ipv4") is not None
                or port.get("target_mac") is not None
            ):
                raise exceptions.ValidateException(
                    err="NodeSet template requires new ports with automatic IP/MAC"
                )
            if (
                isinstance(fip, str)
                or isinstance(fip, dict)
                and fip.get("address") is not None
            ):
                raise exceptions.ValidateException(
                    err="NodeSet template requires new, automatically allocated FIPs"
                )


def create_networking(node, networking, session):
    from exordos_core.compute import constants
    from exordos_core.compute.dm import models as compute
    from exordos_core.user_api.network.dm import models as catalog

    project_id = node.project_id
    generated = (
        node.node_set is not None and node.project_id == constants.NODE_SET_PROJECT
    )
    if generated:
        project_id = compute.NodeSet.objects.get_one(
            filters={"uuid": filters.EQ(node.node_set)}, session=session
        ).project_id
    # Reserve explicit slots first, independent of declaration order.
    for entry in sorted(networking, key=lambda entry: "index" not in entry):
        source = entry["port"]
        if isinstance(source, str):
            catalog.validate_reference(
                project_id, compute.Port, uuid.UUID(source), "port", session=session
            )
            port = compute.Port.objects.get_one(
                filters={"uuid": filters.EQ(source)}, session=session, locked=True
            )
            if port.node is not None:
                raise storage_exc.ConflictRecords(
                    model="Port", msg="existing port must be detached"
                )
            port.activation_barrier = node.network_activation_barrier
            port.node = node.uuid
            port.index = entry.get("index")
            port.update(session=session)
        else:
            port = compute.Port.restore_from_simple_view(
                **{
                    **source,
                    "project_id": str(project_id),
                    "node": str(node.uuid),
                    **({"index": entry["index"]} if "index" in entry else {}),
                    **(
                        {"provenance": constants.PortProvenance.GENERATED.value}
                        if generated
                        else {}
                    ),
                }
            )
            port.activation_barrier = node.network_activation_barrier
            port.insert(session=session)
        for key, model, field in (
            ("security_groups", catalog.PortSecurityGroup, "security_group"),
            ("identity_groups", catalog.PortIdentityGroup, "identity_group"),
        ):
            for group in entry.get(key, []):
                if (
                    model.objects.get_one_or_none(
                        filters={
                            "port": filters.EQ(port.uuid),
                            field: filters.EQ(group),
                        },
                        session=session,
                    )
                    is not None
                ):
                    continue
                binding = model(
                    project_id=project_id,
                    port=port.uuid,
                    **{field: uuid.UUID(str(group))},
                )
                binding.insert(session=session)
        if "floating_ip" in entry:
            fip = entry["floating_ip"]
            if isinstance(fip, str):
                catalog.validate_reference(
                    project_id,
                    catalog.Address,
                    uuid.UUID(fip),
                    "floating_ip",
                    session=session,
                )
                address = catalog.Address.objects.get_one(
                    filters={"uuid": filters.EQ(fip)}, session=session, locked=True
                )
                if address.kind != "floating" or address.port is not None:
                    raise storage_exc.ConflictRecords(
                        model="Address", msg="existing FIP must be unbound"
                    )
                address.port = port.uuid
                address.update(session=session)
            else:
                address = catalog.Address.restore_from_simple_view(
                    **{
                        **fip,
                        "kind": "floating",
                        "port": str(port.uuid),
                        "project_id": str(project_id),
                    }
                )
                address.insert(session=session)
