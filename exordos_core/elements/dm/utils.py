#    Copyright 2025 Genesis Corporation.
#
#    All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import dataclasses
import logging
import os
import re
import typing as tp
import uuid as sys_uuid

from jsonschema.exceptions import ValidationError
import openapi_schema_validator
import yaml

from exordos_core.common import constants as c
from exordos_core.common import exceptions
from exordos_core.common import openapi
from exordos_core.common.utils import PROJECT_PATH
from exordos_core.common.utils import get_api_client
from exordos_core.common.utils import validate_url

LOG = logging.getLogger(__name__)
ELEMENT_NAMESPACE = sys_uuid.UUID("f277e88a-cd58-4c33-a0c3-23a1086a53b7")
SCHEMA_REF_PREFIX = "#/components/schemas/"
UUID_PREFIX = "12345678"
REGEXP = re.compile(r"\$(.+?)(?:\:(.+))?$")
PARENT_LINK_REGEXP = re.compile(r"^\$parent((?:\.parent)*)(.*):(.+)$")
PARENT_LINK_IN_FSTRING_REGEXP = re.compile(r"([\\]?)\{(\$parent(?:\.[^{}:]+)*:[^{}]+)}")


@dataclasses.dataclass
class Parsed:
    input_string: str
    valid: bool = True
    error: str | None = None
    resource_type: str | None = None
    element_name: str | None = None
    resource_paths: list[str] | None = None
    resource_name: str | None = None
    value: str | None = None
    is_variable: bool = False
    is_resource: bool = False
    is_resource_type: bool = False


def clear_parameters(resource_link: str) -> str:
    """
    Example:
        input_string: "resource.type.name:parameter"
        output_string: "resource.type.name"
    """
    parts = resource_link.split(".")
    return ".".join(parts[:-1] + [parts[-1].split(":")[0]])


class ResourceLink:
    def __init__(self, link_string):
        super().__init__()
        self._link_string = link_string

    @property
    def location(self):
        return clear_parameters(self._link_string)

    @property
    def parameter(self):
        return self._link_string.split(".")[-1]


def get_element_uuid(element_name, element_version):
    tmp = str(
        sys_uuid.uuid5(
            ELEMENT_NAMESPACE,
            f"{element_name}-{element_version}",
        )
    )
    return sys_uuid.UUID(f"{UUID_PREFIX}{tmp[8:]}")


def get_project_id():
    # return sys_uuid.UUID(f"{UUID_PREFIX}{str(sys_uuid.uuid4())[8:]}")
    return sys_uuid.UUID(f"{UUID_PREFIX}{str(c.ZERO_UUID)[8:]}")


def get_required_field(data, field_name):
    if field_name not in data:
        raise ValueError(
            f"required field '{field_name}' is missing in the provided data: {data}"
        )
    return data[field_name]


def parse_variable(var: str) -> Parsed:

    res = re.findall(REGEXP, var)

    result = Parsed(input_string=var)
    if not res or len(res[0]) == 0:
        result.valid = False
        result.error = "Invalid format"
        return result

    parts = res[0][0].split(".")

    if len(parts) <= 1:
        result.valid = False
        result.error = "Invalid format"
        return result

    value, resource_parts, resource_name, resource_type = None, None, None, None
    is_variable, is_resource, is_resource_type = False, False, False
    element_name = parts[0]

    if len(parts) == 2:
        is_resource_type = True
        resource_parts = parts[1:]
    elif len(parts) == 3:
        if parts[-1].startswith("$"):
            resource_name = parts[-1][1:]
            resource_parts = parts[1:-1]
            is_resource = True
            resource_type = f"${element_name}.{'.'.join(resource_parts)}"
        else:
            is_resource_type = True
            resource_parts = parts[1:]
    elif len(parts) > 3:
        if len(res[0]) == 2 and res[0][1]:
            value = res[0][1]

        if parts[-1].startswith("$"):
            resource_name = parts[-1][1:]
            resource_parts = parts[1:-1]
            is_resource = True
            is_variable = True if value else False
            resource_type = f"${element_name}.{'.'.join(resource_parts)}"
        elif value:
            result.valid = False
            result.error = "Not found resource"
            return result
        else:
            is_resource_type = True
            resource_parts = parts[1:]

    result.element_name = element_name
    result.resource_paths = resource_parts
    result.resource_name = resource_name
    result.value = value
    result.is_variable = is_variable
    result.is_resource = is_resource
    result.is_resource_type = is_resource_type
    result.resource_type = resource_type

    return result


def walk(node: tp.Union[dict, list, str]) -> tp.List[Parsed]:
    variables = []
    if isinstance(node, dict):
        for value in node.values():
            variables += walk(value)
    elif isinstance(node, list):
        for item in node:
            variables += walk(item)
    else:
        re_vars = [parse_variable(str(node))]
        if re_vars:
            variables.extend(re_vars)
    return variables


def walk_replace(resource_type: str, scheme: dict, node: tp.Union[dict, list, str]):
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str):
                parsed_var = parse_variable(value)
                if parsed_var.valid and parsed_var.is_variable:
                    if parsed_var.resource_type == "$core.vs.variables":
                        example = search_parameter_example(scheme, resource_type, key)
                    else:
                        example = search_parameter_example(
                            scheme, parsed_var.resource_type, parsed_var.value
                        )
                    if example is not None:
                        node[key] = example
            else:
                walk_replace(resource_type, scheme, value)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            if isinstance(item, str):
                parsed_var = parse_variable(item)
                if parsed_var.valid and parsed_var.is_variable:
                    if parsed_var.resource_type == "$core.vs.variables":
                        example = search_parameter_example(scheme, resource_type, item)
                    else:
                        example = search_parameter_example(
                            scheme, parsed_var.resource_type, parsed_var.value
                        )
                    if example is not None:
                        node[i] = example
            else:
                walk_replace(resource_type, scheme, item)


def load_base_manifest_schema() -> dict:
    with open(
        os.path.join(
            PROJECT_PATH, "exordos", "manifests", "specification", "base_spec.yaml"
        ),
        "r",
    ) as f:
        return yaml.safe_load(f)


def load_full_manifest_schema() -> dict:
    with open(
        os.path.join(
            PROJECT_PATH, "exordos", "manifests", "specification", "full_spec.yaml"
        ),
        "r",
    ) as f:
        return yaml.safe_load(f)


def dump_full_manifest_schema(data):
    with open(
        os.path.join(
            PROJECT_PATH, "exordos", "manifests", "specification", "full_spec.yaml"
        ),
        "w",
    ) as f:
        yaml.safe_dump(data, f)


def load_user_api_spec() -> dict:
    """The user API document the manifest schema is derived from.

    Built rather than read: it describes the code in this checkout, and a
    copy on disk under `docs/` is a copy that goes stale on its own.
    """
    return openapi.build(openapi.USER_API)


def validate_manifest(data: dict, schema: tp.Optional[dict]) -> None:
    if data and schema:
        try:
            openapi_schema_validator.validate(
                data, schema, cls=openapi_schema_validator.OAS30Validator
            )
        except ValidationError as err:
            LOG.exception("Failed to validate data %s: %s", data, err)
            raise exceptions.OpenApiValidateException(
                err=f"{err.message} in {err.json_path}"
            )
    return None


def iter_schema_refs(node: tp.Any) -> tp.Iterator[str]:
    """Names of every component schema ``node`` references, at any depth."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith(SCHEMA_REF_PREFIX):
            yield ref[len(SCHEMA_REF_PREFIX) :]
        for value in node.values():
            yield from iter_schema_refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from iter_schema_refs(item)


def copy_schema_closure(source: dict, target: dict, name: str) -> None:
    """Copy ``name`` into ``target`` along with everything it references.

    Copying the named schema alone was enough while every model spelled its
    own properties out. It stopped being enough once a create schema became
    an alias (``Node_Create: {$ref: Node_Get}``): the alias travelled and
    what it pointed at did not, leaving the manifest schema holding
    references to components that were not in it.
    """
    pending = [name]
    while pending:
        current = pending.pop()
        if current in target or current not in source:
            continue
        schema = source[current]
        target[current] = schema
        pending.extend(iter_schema_refs(schema))


def resolve_schema(schemas: dict, name: str) -> tp.Optional[dict]:
    """Follow alias schemas to the one that describes the fields.

    A create schema may be a bare ``$ref`` to the model it shares a shape
    with, so a caller after ``properties`` has to be handed the target and
    not the alias.
    """
    seen: set[str] = set()
    while name not in seen:
        seen.add(name)
        schema = schemas.get(name)
        if schema is None:
            return None
        ref = schema.get("$ref")
        if not (isinstance(ref, str) and ref.startswith(SCHEMA_REF_PREFIX)):
            return schema
        name = ref[len(SCHEMA_REF_PREFIX) :]
    return None


def build_full_schema(
    base_manifest_schema: dict, user_api_spec: dict, specs: list | None = None
) -> dict:
    for path, path_obj in user_api_spec["paths"].items():
        path_parts = path.split("/")
        post_path = path_obj.get("post")
        if post_path:
            operation_id = post_path.get("operationId")
            if operation_id and operation_id.startswith("Create_v1"):
                schema_ref = post_path["requestBody"]["content"]["application/json"][
                    "schema"
                ]
                # A create body that is not a named model is no resource type
                # a manifest can declare — there is nothing to name it by.
                # One such path exists (`/v1/iam/idp/{…}/.well_known/`, an
                # inline `type: object`), and reading `$ref` off it blindly
                # took the whole schema build down with a bare KeyError.
                if "$ref" not in schema_ref:
                    continue
                model_name = schema_ref["$ref"].split("/")[-1]
                api_parts = ".".join(
                    [
                        path_part
                        for path_part in path_parts[2:]
                        if path_part and not path_part.startswith("{")
                    ]
                )
                model = user_api_spec["components"]["schemas"].get(model_name)
                if not model:
                    continue
                resource = f"$core.{api_parts}"
                model["path"] = path
                copy_schema_closure(
                    user_api_spec["components"]["schemas"],
                    base_manifest_schema["components"]["schemas"],
                    model_name,
                )
                base_manifest_schema["components"]["schemas"][model_name] = model
                base_manifest_schema["properties"]["resources"]["properties"][
                    resource
                ] = {
                    "type": "object",
                    "additionalProperties": schema_ref,
                }
    if specs is not None:
        for spec in specs:
            if validate_url(spec):
                client = get_api_client()
                try:
                    response = client.get(spec)
                    if response.status_code == 200:
                        data = response.content.decode()
                        user_api_spec = yaml.safe_load(data)
                        base_manifest_schema = build_full_schema(
                            base_manifest_schema, user_api_spec
                        )
                except Exception as e:
                    LOG.exception(f"Failed to get spec from {spec}: {e}")
            elif os.path.exists(spec):
                try:
                    with open(spec, "r") as f:
                        user_api_spec = yaml.safe_load(f)
                        base_manifest_schema = build_full_schema(
                            base_manifest_schema, user_api_spec
                        )
                except Exception as e:
                    LOG.exception(f"Failed to get spec from {spec}: {e}")
    return base_manifest_schema


def search_parameter_example(
    scheme: dict, resource_type: str, parameter: str
) -> tp.Any:
    resource_type = scheme["properties"]["resources"]["properties"].get(resource_type)
    if not resource_type:
        return None
    ref = resource_type.get("additionalProperties", {}).get("$ref")
    if not ref:
        return None
    model_name = ref.split("/")[-1]
    # Through the alias, if the create schema is one: an alias has no
    # `properties` of its own, and reading them off it raised rather than
    # returning "no example for this parameter".
    model = resolve_schema(scheme["components"]["schemas"], model_name)
    if model:
        example = model["properties"].get(parameter, {}).get("example")
        if example is not None:
            return example
        for prop in model["properties"].values():
            if "oneOf" in prop:
                for oneOf in prop["oneOf"]:
                    example = oneOf["properties"].get(parameter, {}).get("example")
                    if example is not None:
                        return example
    return None


def remove_middle_parts(input_string):
    parts = input_string.split(".")

    if len(parts) <= 2:
        return input_string

    result_parts = []
    for i, part in enumerate(parts):
        if i > 0 and part.startswith("$"):
            continue
        result_parts.append(part)
    return ".".join(result_parts)


def _resolve_parent_links(value, ancestors: list[str], resource_link: str):
    if isinstance(value, dict):
        return {
            key: _resolve_parent_links(item, ancestors, resource_link)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_resolve_parent_links(item, ancestors, resource_link) for item in value]
    if not isinstance(value, str):
        return value

    def resolve(link: str) -> str:
        match = PARENT_LINK_REGEXP.fullmatch(link)
        if not match:
            raise exceptions.ValidateException(
                err=f"Invalid parent link '{link}' in resource '{resource_link}'."
            )
        level = 1 + match.group(1).count(".parent")
        if level > len(ancestors):
            raise exceptions.ValidateException(
                err=f"Parent link '{link}' in resource '{resource_link}' "
                f"goes above the root resource."
            )
        relative_path = match.group(2)
        if relative_path:
            parts = (
                relative_path[1:].split(".") if relative_path.startswith(".") else []
            )
            if (
                not parts
                or len(parts) % 2
                or any(not part for part in parts)
                or any(
                    parts[index].startswith("$") for index in range(0, len(parts), 2)
                )
                or any(
                    not parts[index].startswith("$") or len(parts[index]) == 1
                    for index in range(1, len(parts), 2)
                )
            ):
                raise exceptions.ValidateException(
                    err=f"Invalid relative resource path in parent link '{link}' "
                    f"in resource '{resource_link}'."
                )
        return f"{ancestors[level - 1]}{relative_path}:{match.group(3)}"

    if value.startswith('f"'):

        def replace_inline(match):
            if match.group(1):
                return match.group(0)
            return "{" + resolve(match.group(2)) + "}"

        return PARENT_LINK_IN_FSTRING_REGEXP.sub(replace_inline, value)

    if value == "$parent" or value.startswith(("$parent:", "$parent.")):
        return resolve(value)
    return value


def _expand_resource_group(
    resource_type: str,
    resources: dict,
    ancestors: list[str],
    expanded: dict,
    parent_value: dict | None = None,
) -> None:
    group = expanded.setdefault(resource_type, {})
    if not isinstance(resources, dict) or not isinstance(group, dict):
        raise exceptions.ValidateException(
            err=f"Nested resources for '{resource_type}' must be a mapping."
        )

    for resource_name, resource_value in resources.items():
        resource_link = f"{resource_type}.${resource_name}"
        child_collections = []
        value = resource_value
        if isinstance(resource_value, dict):
            value = {}
            for key, item in resource_value.items():
                if isinstance(key, str) and key.startswith("/"):
                    collection = key[1:]
                    if (
                        not collection
                        or collection.startswith("$")
                        or "." in collection
                        or "/" in collection
                    ):
                        raise exceptions.ValidateException(
                            err=f"Invalid nested collection key '{key}' "
                            f"in resource '{resource_link}'."
                        )
                    if not isinstance(item, dict):
                        raise exceptions.ValidateException(
                            err=f"Nested collection '{key}' in resource "
                            f"'{resource_link}' must be a mapping."
                        )
                    child_collections.append((collection, item))
                else:
                    value[key] = item

        if (
            isinstance(value, dict)
            and parent_value is not None
            and isinstance(parent_value, dict)
            and "project_id" not in value
            and "project_id" in parent_value
        ):
            value["project_id"] = parent_value["project_id"]

        if ancestors:
            value = _resolve_parent_links(value, ancestors, resource_link)

        if resource_name in group:
            raise exceptions.ValidateException(
                err=f"Resource '{resource_name}' is declared more than once "
                f"in '{resource_type}'."
            )
        group[resource_name] = value

        for collection, child_resources in child_collections:
            _expand_resource_group(
                resource_type=f"{resource_link}.{collection}",
                resources=child_resources,
                ancestors=[resource_link, *ancestors],
                expanded=expanded,
                parent_value=value,
            )


def expand_nested_resources(resources: dict) -> dict:
    """Expand slash-prefixed child collections into flat resource groups."""
    expanded = {}
    for resource_type, resource_group in resources.items():
        _expand_resource_group(resource_type, resource_group, [], expanded)
    return expanded


def mutate_resource_types(manifest: dict) -> dict:
    mutated_map = {}
    for resource_type in manifest["resources"].keys():
        mutated_resource_type = remove_middle_parts(resource_type)
        if resource_type != mutated_resource_type:
            mutated_map[mutated_resource_type] = resource_type
    for mutated_resource_type, resource_type in mutated_map.items():
        manifest["resources"][mutated_resource_type] = manifest["resources"].pop(
            resource_type
        )
    return manifest


def mutate_manifest(manifest: dict, scheme: dict) -> dict:
    manifest["resources"] = expand_nested_resources(manifest["resources"])
    manifest = mutate_resource_types(manifest)
    for resource_type, resource in manifest["resources"].items():
        for resource_name, resource_value in resource.items():
            walk_replace(resource_type, scheme, resource_value)
    return manifest


def extract_resources_for_markdown(full_schema):

    resources_links = (
        full_schema.get("properties", {}).get("resources", {}).get("properties", {})
    )

    resources = []

    for resource_link_name, resource_ref in resources_links.items():
        ref = resource_ref.get("additionalProperties", {}).get("$ref")
        if not ref:
            continue
        resource_ref = ref.split("/")[-1]
        resource_schema = (
            full_schema.get("components", {}).get("schemas", {}).get(resource_ref)
        )
        if not resource_schema:
            continue
        resources.append(
            {
                "Entity": resource_ref.replace("_Create", ""),
                "Api": resource_schema.get("path"),
                "Manifest": resource_link_name,
            }
        )

    return resources


def generate_resources_markdown_table(resources, headers=("Entity", "Api", "Manifest")):
    entity, api, manifest = headers
    table = f"| {entity} | {api} | {manifest} |\n"
    table += "| ----- | --- | ------- |\n"
    for resource in resources:
        table += (
            f"| {resource['Entity']} | {resource['Api']} | {resource['Manifest']} |\n"
        )
    return table
