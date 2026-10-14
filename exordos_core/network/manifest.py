#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

"""Initial security declarations must be stored before a port is activated."""


def _siblings(engine):
    return [
        item
        for item in engine.get_resources()
        if getattr(item, "element", None) is not None
    ]


def _reference(item, field, engine):
    value = item.value.get(field)
    return (
        str(item._render_value(value, engine)) if isinstance(value, str) else str(value)
    )


def _rules_for(groups, engine):
    return [
        item
        for item in _siblings(engine)
        if getattr(item, "resource_link_prefix", "").endswith(
            ".network.catalog.security_group_rules"
        )
        and _reference(item, "security_group", engine) in groups
    ]


def activation_barrier(resource, target_state, engine):
    bindings = [
        item
        for item in _siblings(engine)
        if getattr(item, "resource_link_prefix", "").endswith(
            ".network.port_security_groups"
        )
        and _reference(item, "port", engine) == str(target_state["uuid"])
    ]
    groups = {_reference(item, "security_group", engine) for item in bindings}
    rules = _rules_for(groups, engine)
    return {
        "bindings": [str(item.value.get("uuid", item.uuid)) for item in bindings],
        "rules": [str(item.value.get("uuid", item.uuid)) for item in rules],
    }


def barrier_ready(barrier, session=None):
    from restalchemy.dm import filters

    from exordos_core.user_api.network.dm import models

    for key, model in (
        ("bindings", models.PortSecurityGroup),
        ("rules", models.SecurityGroupRule),
    ):
        required = set(barrier.get(key, []))
        if not required:
            continue
        found = model.objects.get_all(
            filters={"uuid": filters.In(list(required))}, session=session
        )
        if {str(item.uuid) for item in found} != required:
            return False
    return True
