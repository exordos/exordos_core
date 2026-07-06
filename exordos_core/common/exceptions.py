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

from izulu import root


class GCException(root.Error):
    __toggles__ = root.Toggles.DEFAULT ^ root.Toggles.FORBID_UNANNOTATED_FIELDS
    __template__ = "An unknown exception occurred."


class CommonNotFoundException(GCException):
    __template__ = "The requested resource was not found."


class CommonForbiddenException(GCException):
    __template__ = "Access to the requested resource is forbidden."


class CommonValueErrorException(GCException):
    __template__ = "The provided value is invalid."


class OpenApiValidateException(CommonValueErrorException):
    __template__ = "OpenApiValidateException: {err}"

    err: str


class ValidateException(CommonValueErrorException):
    __template__ = "validate error: {err}"

    err: str


class NamespaceNotFound(GCException):
    __template__ = "Namespace with name '{name}' was not found."

    @property
    def code(self) -> int:
        return 1000


class ManifestNotFound(GCException):
    __template__ = "validate error: {err}"

    err: str
