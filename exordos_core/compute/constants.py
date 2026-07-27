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
import typing as tp
import uuid as sys_uuid

DEF_SQL_LIMIT = 300
EP_MACHINE_POOL_DRIVERS = "gcn_machine_pool_driver"
EP_NETWORK_DRIVERS = "gcn_network_driver"
DEF_ROOT_DISK_SIZE = 10
POLICY_SERVICE_NAME = "compute"

NODE_SET_PROJECT = sys_uuid.UUID("11111113-bc70-4760-9fbf-9fcfe40da329")

# Sentinel UUID for the transient boot-network port (see
# Port.from_boot_network()) - a real UUID doesn't make sense for it since
# it's replaced once the machine is flashed.
BOOT_NETWORK_PORT_UUID = sys_uuid.UUID("00000000-0000-0000-0000-000000000000")


BootType = tp.Literal["hd", "network", "cdrom"]


class NodeStatus(str, enum.Enum):
    NEW = "NEW"
    SCHEDULED = "SCHEDULED"
    IN_PROGRESS = "IN_PROGRESS"
    STARTED = "STARTED"
    ACTIVE = "ACTIVE"
    ERROR = "ERROR"


class MachineStatus(str, enum.Enum):
    NEW = "NEW"
    SCHEDULED = "SCHEDULED"
    IN_PROGRESS = "IN_PROGRESS"
    STARTED = "STARTED"
    ACTIVE = "ACTIVE"
    IDLE = "IDLE"
    ERROR = "ERROR"
    FLASHED = "FLASHED"
    NEED_RESCHEDULE = "NEED_RESCHEDULE"


class VolumeStatus(str, enum.Enum):
    NEW = "NEW"
    IN_PROGRESS = "IN_PROGRESS"
    ACTIVE = "ACTIVE"
    ERROR = "ERROR"


class NodeType(str, enum.Enum):
    VM = "VM"
    HW = "HW"


class VolumeType(str, enum.Enum):
    QCOW2 = "QCOW2"


class BuilderStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class MachineBuildStatus(str, enum.Enum):
    IN_BUILD = "IN_BUILD"
    READY = "READY"


class MachinePoolStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    MAINTENANCE = "MAINTENANCE"
    IN_PROGRESS = "IN_PROGRESS"


class BootAlternative(str, enum.Enum):
    hd0 = "hd0"
    hd1 = "hd1"
    hd2 = "hd2"
    hd3 = "hd3"
    hd4 = "hd4"
    hd5 = "hd5"
    hd6 = "hd6"
    hd7 = "hd7"
    cdrom = "cdrom"
    network = "network"

    @property
    def hd_prefix(self) -> str:
        return "hd"

    @property
    def boot_from_hd(self) -> bool:
        return self.value.startswith(self.hd_prefix)

    @property
    def boot_type(self) -> BootType:
        if self.boot_from_hd:
            return self.hd_prefix
        elif self.value == "cdrom":
            return "cdrom"
        elif self.value == "network":
            return "network"

        raise ValueError(f"Invalid boot alternative: {self.value}")


class PortStatus(str, enum.Enum):
    NEW = "NEW"
    IN_PROGRESS = "IN_PROGRESS"
    ACTIVE = "ACTIVE"
    ERROR = "ERROR"


class PlacementPolicyKind(str, enum.Enum):
    SOFT_ANTI_AFFINITY = "soft-anti-affinity"
