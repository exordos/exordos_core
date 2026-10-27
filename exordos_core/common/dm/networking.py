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
        if name in ("network", "target_ipv4") and isinstance(
            field_type, types.AllowNone
        ):
            field_type = field_type.nested_type
        result[name] = field_type.to_openapi_spec(prop_kwargs.copy())
        if name == "target_ipv4":
            result[name].pop("anyOf", None)
            result[name]["format"] = "ipv4"
    return result


class DefaultNetworkRequestType(types.Dict):
    def __init__(self, template=False):
        self.template = template
        super().__init__()

    def to_openapi_spec(self, prop_kwargs):
        from exordos_core.compute.dm.models import Port

        prop_kwargs = {"openapi": prop_kwargs.get("openapi")}
        fields = {"network", "subnet", "port_security"}
        if not self.template:
            fields.add("target_ipv4")
        properties = _creation_properties(Port, fields, prop_kwargs)
        properties["auto_floating_ip"] = {"type": "boolean"}
        properties["floating_network"] = properties["network"].copy()
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
            "not": {"required": ["network", "subnet"]},
            "anyOf": [
                {"not": {"required": ["floating_network"]}},
                {
                    "required": ["auto_floating_ip"],
                    "properties": {"auto_floating_ip": {"enum": [True]}},
                },
            ],
        }


class DefaultNetworkType(types.AllowNone):
    def __init__(self, template=False):
        super().__init__(DefaultNetworkRequestType(template=template))
