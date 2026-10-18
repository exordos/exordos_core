# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0.
"""HTTP authorization shared by canonical resources and creation wrappers."""

from gcl_iam import rules
from restalchemy.dm import filters

from exordos_core.network.contracts import resolve_subnet


def _ref(value):
    return getattr(value, "uuid", value)


def _require_unmanaged_node(node_uuid):
    from exordos_core.common import exceptions
    from exordos_core.compute.dm import models

    if node_uuid is None:
        return
    node = models.Node.objects.get_one(filters={"uuid": filters.EQ(node_uuid)})
    if node.node_set is not None:
        raise exceptions.ValidateException(
            err="manage replica networking through NodeSet"
        )


def _require_unmanaged_port(port_uuid):
    from exordos_core.compute.dm import models

    port = models.Port.objects.get_one(filters={"uuid": filters.EQ(port_uuid)})
    _require_unmanaged_node(_ref(port.node) or port.owner_node)


def gate_port_ownership(controller, value, stored):
    from exordos_core.compute.dm import models
    from exordos_core.user_api.network.dm.models import validate_reference

    if {"node", "owner_node"} & value.keys():
        _require_unmanaged_node(_ref(stored.node) or stored.owner_node)
    owner = value.get("owner_node")
    if owner is None or str(owner) == str(stored.owner_node):
        return
    validate_reference(stored.project_id, models.Node, owner, "owner_node")
    _require_unmanaged_node(owner)
    controller._enforcer.enforce(rules.Rule("compute", "node", "update"), do_raise=True)


def gate_address_ownership(controller, value, stored):
    from exordos_core.compute.dm import models
    from exordos_core.user_api.network.dm.models import validate_reference

    if {"port", "owner_port"} & value.keys():
        current = stored.port or stored.owner_port
        if current is not None:
            _require_unmanaged_port(current)
    if value.get("port") is not None:
        _require_unmanaged_port(value["port"])
    owner = value.get("owner_port")
    if owner is None or str(owner) == str(stored.owner_port):
        return
    validate_reference(stored.project_id, models.Port, owner, "owner_port")
    _require_unmanaged_port(owner)
    enforce_network(controller, "port", "update")


def enforce_network(controller, resource, action):
    return controller._enforcer.enforce(
        rules.Rule("network", resource, action), do_raise=True
    )


def gate_explicit_address(controller, value, project_id):
    address = value.get("target_ipv4")
    if address is None:
        return
    if value.get("network") is None and value.get("subnet") is None:
        # Authorize automatic placement against the allocated subnet before commit.
        return
    subnet = resolve_subnet(
        project_id,
        subnet=value.get("subnet"),
        network=value.get("network"),
        address=address,
        floating=False,
    )
    if str(subnet.project_id) != str(project_id):
        enforce_network(controller, "address", "explicit_address")


def gate_allocated_private_address(controller, port, session):
    if port.target_ipv4 is None:
        return
    from exordos_core.compute.dm import models

    subnet = models.Subnet.objects.get_one(
        filters={"uuid": filters.EQ(getattr(port.subnet, "uuid", port.subnet))},
        session=session,
    )
    if subnet.project_id != port.project_id:
        enforce_network(controller, "address", "explicit_address")


def gate_default_network(controller, value, project_id):
    if value.get("node_type", "VM") != "VM":
        return
    request = value.get("default_network")
    if request == {}:
        return
    request = request or {}
    gate_explicit_address(controller, request, project_id)
    if request.get("auto_floating_ip"):
        enforce_network(controller, "address", "create")
