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

import logging
import typing as tp

from gcl_sdk.agents.universal.drivers import direct
from gcl_sdk.agents.universal.storage import fs

from exordos_core.agent.universal.drivers.secret.backend import db as db_back

LOG = logging.getLogger(__name__)

PASSWORD_TARGET_FIELDS_STORAGE = (
    "/var/lib/exordos/exordos_core/password_target_fields.json"
)


class PasswordCapabilityDriver(direct.DirectAgentDriver):
    """Password capability driver."""

    def __init__(self, storage_path: str = PASSWORD_TARGET_FIELDS_STORAGE):
        storage = fs.TargetFieldsFileStorage(storage_path)
        client = db_back.DatabaseSecretBackendClient()

        super().__init__(storage=storage, client=client)

    def get_capabilities(self) -> tp.List[str]:
        """Returns a list of capabilities supported by the driver."""
        return ["password"]
