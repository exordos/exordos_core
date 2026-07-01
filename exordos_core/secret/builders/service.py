#    Copyright 2025-2026 Genesis Corporation.
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

from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.agents.universal.services import builder as sdk_builder

from exordos_core.secret import constants as sc
from exordos_core.secret.dm import models

LOG = logging.getLogger(__name__)


class Password(
    models.Password,
    ua_models.InstanceMixin,
    ua_models.KindAwareMixin,
):
    pass

    @classmethod
    def get_resource_kind(cls) -> str:
        return sc.PASSWORD_KIND


class Certificate(
    models.Certificate,
    ua_models.InstanceMixin,
    ua_models.KindAwareMixin,
):
    pass

    @classmethod
    def get_resource_kind(cls) -> str:
        return sc.CERTIFICATE_KIND


class RSAKey(
    models.RSAKey,
    ua_models.InstanceMixin,
    ua_models.KindAwareMixin,
):
    pass

    @classmethod
    def get_resource_kind(cls) -> str:
        return sc.RSA_KEY_KIND


class SSHKey(
    models.SSHKey,
    ua_models.InstanceMixin,
    ua_models.KindAwareMixin,
):
    pass

    @classmethod
    def get_resource_kind(cls) -> str:
        return sc.SSH_KEY_KIND


class PasswordBuilder(sdk_builder.UniversalBuilderService):
    def __init__(
        self,
        iter_min_period: int = 1,
        iter_pause: float = 0.1,
    ) -> None:
        super().__init__(
            instance_model=Password,
            iter_min_period=iter_min_period,
            iter_pause=iter_pause,
        )

    def actualize_outdated_instance(
        self,
        current_instance: Password,
        actual_instance: Password,
    ) -> None:
        password_updated = False
        status_updated = False
        if actual_instance.status != current_instance.status:
            current_instance.status = actual_instance.status
            status_updated = True
        if actual_instance.value != current_instance.value:
            current_instance.value = actual_instance.value
            password_updated = True
        if status_updated or password_updated:
            current_instance.save()
