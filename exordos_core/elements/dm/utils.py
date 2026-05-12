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

from exordos_core.common import exceptions
from exordos_core.common.utils import PROJECT_PATH
from exordos_core.common.utils import get_api_client
from exordos_core.common.utils import validate_url

LOG = logging.getLogger(__name__)
ELEMENT_NAMESPACE = sys_uuid.UUID("f277e88a-cd58-4c33-a0c3-23a1086a53b7")
UUID_PREFIX = "12345678"
REGEXP = re.compile(r"\$(.+?)(?:\:(.+))?$")


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
    return sys_uuid.UUID(
        f"{UUID_PREFIX}{str('00000000-0000-0000-0000-000000000000')[8:]}"
    )


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
            PROJECT_PATH, "genesis", "manifests", "specification", "base_spec.yaml"
        ),
        "r",
    ) as f:
        return yaml.safe_load(f)


def load_full_manifest_schema() -> dict:
    with open(
        os.path.join(
            PROJECT_PATH, "genesis", "manifests", "specification", "full_spec.yaml"
        ),
        "r",
    ) as f:
        return yaml.safe_load(f)


def dump_full_manifest_schema(data):
    with open(
        os.path.join(
            PROJECT_PATH, "genesis", "manifests", "specification", "full_spec.yaml"
        ),
        "w",
    ) as f:
        yaml.safe_dump(data, f)


def dump_api_spec(data):
    with open(
        os.path.join(PROJECT_PATH, "docs", "em", "api_documentation.md"),
        "w",
    ) as f:
        f.write(data)


def load_user_api_spec() -> dict:
    with open(
        os.path.join(PROJECT_PATH, "docs", "openapi", "openapi_user.yaml"),
        "r",
    ) as f:
        return yaml.safe_load(f)


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
    model = scheme["components"]["schemas"].get(model_name)
    if model:
        return model["properties"].get(parameter, {}).get("example")
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


def generate_resources_markdown_table(resources):
    table = "| Entity | Api | Manifest |\n"
    table += "| ----- | --- | ------- |\n"
    for resource in resources:
        table += (
            f"| {resource['Entity']} | {resource['Api']} | {resource['Manifest']} |\n"
        )
    return table
