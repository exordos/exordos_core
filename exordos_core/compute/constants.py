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

import enum
import uuid as sys_uuid

from gcl_sdk.agents.universal.drivers.pool import BootAlternative  # noqa: F401
from gcl_sdk.agents.universal.drivers.pool import BootType  # noqa: F401
from gcl_sdk.agents.universal.drivers.pool import MachinePoolStatus  # noqa: F401
from gcl_sdk.agents.universal.drivers.pool import MachineStatus  # noqa: F401
from gcl_sdk.agents.universal.drivers.pool import NodeType  # noqa: F401
from gcl_sdk.agents.universal.drivers.pool import PortStatus  # noqa: F401
from gcl_sdk.agents.universal.drivers.pool import VolumeStatus  # noqa: F401

DEF_SQL_LIMIT = 300
EP_NETWORK_DRIVERS = "gcn_network_driver"
DEF_ROOT_DISK_SIZE = 10
POLICY_SERVICE_NAME = "compute"

NODE_SET_PROJECT = sys_uuid.UUID("11111113-bc70-4760-9fbf-9fcfe40da329")


class NodeStatus(str, enum.Enum):
    NEW = "NEW"
    SCHEDULED = "SCHEDULED"
    IN_PROGRESS = "IN_PROGRESS"
    STARTED = "STARTED"
    ACTIVE = "ACTIVE"
    ERROR = "ERROR"


class VolumeType(str, enum.Enum):
    QCOW2 = "QCOW2"


class BuilderStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class MachineBuildStatus(str, enum.Enum):
    IN_BUILD = "IN_BUILD"
    READY = "READY"


class PlacementPolicyKind(str, enum.Enum):
    SOFT_ANTI_AFFINITY = "soft-anti-affinity"
