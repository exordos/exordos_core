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

SECRET_KIND = "secret"
PASSWORD_KIND = "password"
CERTIFICATE_KIND = "certificate"
RSA_KEY_KIND = "rsa_key"
SSH_KEY_KIND = "ssh_key"
SSH_KEY_TARGET_KIND = "ssh_key_target"

AUTHORIZED_KEYS_PATH = ".ssh/authorized_keys"


class SecretStatus(str, enum.Enum):
    NEW = "NEW"
    IN_PROGRESS = "IN_PROGRESS"
    ACTIVE = "ACTIVE"
    ERROR = "ERROR"


class SecretMethod(str, enum.Enum):
    AUTO_HEX = "AUTO_HEX"
    AUTO_URL_SAFE = "AUTO_URL_SAFE"
    MANUAL = "MANUAL"

    @property
    def is_auto(self):
        return self in {SecretMethod.AUTO_HEX, SecretMethod.AUTO_URL_SAFE}
