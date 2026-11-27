# Copyright 2025 Genesis Corporation
#
# All Rights Reserved.
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

from gcl_sdk.events.dm import models as sdk
from restalchemy.dm import properties
from restalchemy.dm import types

from exordos_core.events import constants as event_c


class RegistrationEventPayload(sdk.AbstractEventPayload):
    KIND = event_c.IAM_USER_REGISTRATION_EVENT

    site_endpoint = properties.property(
        types.Url(),
        required=True,
    )
    confirmation_code = properties.property(
        types.UUID(),
        required=True,
    )

    def to_simple_dict(self):
        return {
            "site_endpoint": self.site_endpoint,
            "confirmation_code": str(self.confirmation_code),
        }


class ResetPasswordEventPayload(sdk.AbstractEventPayload):
    KIND = event_c.IAM_USER_RESET_PASSWORD_EVENT

    site_endpoint = properties.property(
        types.Url(),
        required=True,
    )
    reset_code = properties.property(
        types.UUID(),
        required=True,
    )

    def to_simple_dict(self):
        return {
            "site_endpoint": self.site_endpoint,
            "reset_code": str(self.reset_code),
        }


class EmailChangeEventPayload(sdk.AbstractEventPayload):
    KIND = event_c.IAM_USER_EMAIL_CHANGE_EVENT

    recipient = properties.property(types.Email(max_length=128), required=True)
    code = properties.property(types.String(max_length=128), default="")

    def to_simple_dict(self):
        return {"recipient": self.recipient, "code": self.code}
