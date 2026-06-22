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

from importlib.metadata import entry_points
import os
import typing as tp
from urllib.parse import urlparse
import uuid as sys_uuid

import bazooka
from gcl_sdk.agents.universal import utils as sdk_utils
from gcl_sdk.events import clients as sdk_clients
from restalchemy.common import contexts
from restalchemy.dm import filters as dm_filters

from exordos_core.common import constants as c
from exordos_core.user_api.iam import constants as iam_c


def node_uuid(path: str = c.NODE_UUID_PATH) -> sys_uuid.UUID:
    return sdk_utils.node_uuid(node_path=path)


def load_from_entry_point(group: str, name: str) -> tp.Any:
    """Load class from entry points."""
    for ep in entry_points():
        if ep.group == group and ep.name == name:
            return ep.load()

    raise RuntimeError(f"No class '{name}' found in entry points {group}")


def load_group_from_entry_point(group: str) -> tp.Any:
    """Load class from entry points."""
    return [e for e in entry_points(group=group)]


def get_context_storage(
    global_salt: str,
    hs256_jwks_encryption_key: str,
    events_client: sdk_clients.AbstractEventClient,
) -> contexts.Storage:
    return contexts.Storage(
        data={
            iam_c.STORAGE_KEY_IAM_GLOBAL_SALT: {
                "value": global_salt,
                "read_only": True,
            },
            iam_c.STORAGE_KEY_IAM_HS256_JWKS_ENCRYPTION_KEY: {
                "value": hs256_jwks_encryption_key,
                "read_only": True,
            },
            iam_c.STORAGE_KEY_EVENTS_CLIENT: {
                "value": events_client,
                "read_only": True,
            },
        }
    )


def remove_all_dm(dm_class, filters, session=None, **kwargs):
    for dm in dm_class.objects.get_all(filters=filters, session=session):
        dm.delete(session=session, **kwargs)


def remove_nested_dm(dm_class, parent_field_name, parent, session=None, **kwargs):
    remove_all_dm(
        dm_class,
        filters={parent_field_name: dm_filters.EQ(parent)},
        session=session,
        **kwargs,
    )


def get_or_create_uuid_from_dict(data: tp.Dict[str, tp.Any]) -> sys_uuid.UUID:
    return sys_uuid.UUID(data.get("uuid", str(sys_uuid.uuid4())))


def validate_url(url: str) -> bool:
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except ValueError:
        return False


def get_api_client(*args, **kwargs) -> bazooka.Client:
    return bazooka.Client(*args, **kwargs)


def get_project_path() -> str:
    # Repository path
    return os.sep.join(__file__.split(os.sep)[:-3])


def urn(namespace: str, uuid: sys_uuid.UUID | str) -> str:
    return f"urn:{namespace}:{uuid}"


def urn_parse(urn: str) -> tuple[str, sys_uuid.UUID]:
    namespace, uuid = urn.split(":")[1:]
    return namespace, sys_uuid.UUID(uuid)


PROJECT_PATH = get_project_path()
