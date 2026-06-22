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

import uuid as sys_uuid

from exordos_core.common import exceptions


class MachineAlreadyExistsError(exceptions.GCException):
    __template__ = "The machine {machine} already exists."
    machine: sys_uuid.UUID


class MachineNotFoundError(exceptions.GCException):
    __template__ = "The machine {machine} not found."
    machine: sys_uuid.UUID


class VolumeAlreadyExistsError(exceptions.GCException):
    __template__ = "The volume {volume} already exists."
    volume: sys_uuid.UUID


class VolumeNotFoundError(exceptions.GCException):
    __template__ = "The volume {volume} not found."
    volume: sys_uuid.UUID


class VolumeAlreadyAttachedError(exceptions.GCException):
    __template__ = "The volume {volume} is already attached to machine {machine}."
    volume: sys_uuid.UUID
    machine: sys_uuid.UUID


class VolumeNotAttachedError(exceptions.GCException):
    __template__ = "The volume {volume} is not attached to machine {machine}."
    volume: sys_uuid.UUID
    machine: sys_uuid.UUID


class PortAlreadyAttachedError(exceptions.GCException):
    __template__ = "The port {port} is already attached to machine {machine}."
    port: sys_uuid.UUID
    machine: sys_uuid.UUID


class PortNotAttachedError(exceptions.GCException):
    __template__ = "The port {port} is not attached to machine {machine}."
    port: sys_uuid.UUID
    machine: sys_uuid.UUID
