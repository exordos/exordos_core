# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0.
"""HTTP authorization shared by canonical resources and creation wrappers."""

from gcl_iam import rules

from exordos_core.network.contracts import resolve_subnet


def enforce_network(controller, resource, action):
    return controller._enforcer.enforce(
        rules.Rule("network", resource, action), do_raise=True
    )


def gate_explicit_address(controller, value, project_id, *, private=False):
    address = value.get("target_ipv4" if private else "address")
    if address is None:
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


def gate_networking(controller, networking, project_id):
    for entry in networking or []:
        port = entry["port"]
        enforce_network(
            controller, "port", "update" if isinstance(port, str) else "create"
        )
        if isinstance(port, dict):
            gate_explicit_address(controller, port, project_id, private=True)
        for key, resource in (
            ("security_groups", "port_security_group"),
            ("identity_groups", "port_identity_group"),
        ):
            if entry.get(key):
                enforce_network(controller, resource, "create")
        fip = entry.get("floating_ip")
        if fip is not None:
            enforce_network(
                controller, "address", "update" if isinstance(fip, str) else "create"
            )
            if isinstance(fip, dict):
                gate_explicit_address(
                    controller, {**fip, "kind": "floating"}, project_id
                )
