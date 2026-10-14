# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0.
"""HTTP authorization shared by canonical resources and creation wrappers."""

from gcl_iam import rules
from restalchemy.dm import filters

from exordos_core.network.contracts import resolve_subnet


def enforce_network(controller, resource, action):
    return controller._enforcer.enforce(
        rules.Rule("network", resource, action), do_raise=True
    )


def gate_explicit_address(controller, value, project_id, *, private=False):
    address = value.get("target_ipv4" if private else "address")
    if address is None:
        return
    if private and value.get("network") is None and value.get("subnet") is None:
        # Authorize automatic placement against the allocated subnet before commit.
        return
    subnet = resolve_subnet(
        project_id,
        subnet=value.get("subnet"),
        network=value.get("network"),
        address=address,
        floating=False
        if private
        else True
        if value.get("kind") == "floating"
        else None,
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
    if value.get("interfaces") is not None:
        return
    if value.get("node_type", "VM") != "VM":
        return
    request = value.get("default_network")
    if request == {}:
        return
    request = request or {}
    gate_explicit_address(controller, request, project_id, private=True)
    if "floating_ip" in request:
        enforce_network(controller, "address", "create")
        if isinstance(request["floating_ip"], dict):
            gate_explicit_address(
                controller, {**request["floating_ip"], "kind": "floating"}, project_id
            )
