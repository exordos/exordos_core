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

DEF_SQL_LIMIT = 300
EP_NETWORK_DRIVERS = "gcn_network_driver"
DEF_ROOT_DISK_SIZE = 10
POLICY_SERVICE_NAME = "compute"

NODE_SET_PROJECT = sys_uuid.UUID("11111113-bc70-4760-9fbf-9fcfe40da329")

# The network driver that makes a network an overlay.
OVERLAY_DRIVER = "ovs_evpn"
OVERLAY_MTU_RANGE = (576, 9000)


def is_overlay(driver_spec) -> bool:
    """Does a network with this ``driver_spec`` carry an overlay?"""
    return isinstance(driver_spec, dict) and driver_spec.get("driver") == OVERLAY_DRIVER


def default_group_name(subnet_uuid) -> str:
    """The name of an overlay subnet's default identity group."""
    return "subnet-%s" % subnet_uuid


# Ports the platform answers on in every overlay namespace (metadata, boot,
# orch, status). Mirrors `PLATFORM_PORTS` in the data plane's proxy; a door
# must not take one, or a caller would receive every agent's reports.
PLATFORM_PROXY_PORTS = frozenset((80, 11011, 11012, 11013))


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


class PortProvenance(str, enum.Enum):
    # As `NFProvenance`: `generated` rows are the compiler's to rewrite or
    # remove; `user` rows it only reads.
    USER = "user"
    GENERATED = "generated"


class PlacementPolicyKind(str, enum.Enum):
    SOFT_ANTI_AFFINITY = "soft-anti-affinity"
