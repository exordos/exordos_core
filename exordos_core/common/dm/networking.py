# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0.
"""Creation request schema, separate from stored and observed port state."""

from restalchemy.dm import types


def _creation_properties(model, fields, prop_kwargs):
    field_types = {
        name: prop.get_property_type()
        for name, prop in model.properties.properties.items()
    } | dict(model.get_custom_properties())
    result = {}
    for name in sorted(fields):
        field_type = field_types[name]
        if name in ("network", "target_ipv4", "target_mac", "address") and isinstance(
            field_type, types.AllowNone
        ):
            field_type = field_type.nested_type
        result[name] = field_type.to_openapi_spec(prop_kwargs.copy())
        if name in ("target_ipv4", "address"):
            result[name].pop("anyOf", None)
            result[name]["format"] = "ipv4"
    return result


class NetworkingType(types.AllowNone):
    def __init__(self, template=False):
        self.template = template
        super().__init__(types.TypedList(types.Dict()))

    def to_openapi_spec(self, prop_kwargs):
        from exordos_core.compute.dm.models import Port
        from exordos_core.network.contracts import INLINE_FLOATING_CREATE_FIELDS
        from exordos_core.network.contracts import INLINE_PORT_CREATE_FIELDS
        from exordos_core.user_api.network.dm.models import Address

        prop_kwargs = {"openapi": prop_kwargs.get("openapi")}
        reference = {"type": "string", "format": "uuid"}
        selectors = [
            {"required": ["subnet"], "not": {"required": ["network"]}},
            {"required": ["network"], "not": {"required": ["subnet"]}},
        ]
        port = {
            "type": "object",
            "additionalProperties": False,
            "oneOf": selectors,
            "properties": _creation_properties(
                Port, INLINE_PORT_CREATE_FIELDS, prop_kwargs
            ),
        }
        fip = {
            "type": "object",
            "additionalProperties": False,
            "oneOf": selectors,
            "properties": _creation_properties(
                Address, INLINE_FLOATING_CREATE_FIELDS, prop_kwargs
            ),
        }
        if self.template:
            port["properties"].pop("target_ipv4")
            port["properties"].pop("target_mac")
            fip["properties"].pop("address")
        return {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["port"],
                "properties": {
                    "port": port if self.template else {"oneOf": [reference, port]},
                    "index": {"type": "integer", "minimum": 0},
                    "security_groups": {"type": "array", "items": reference},
                    "identity_groups": {"type": "array", "items": reference},
                    "floating_ip": fip
                    if self.template
                    else {"oneOf": [reference, fip]},
                },
            },
        }
