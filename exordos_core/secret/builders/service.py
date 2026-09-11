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


class Secret(
    models.Secret,
    ua_models.InstanceMixin,
    ua_models.ReadinessMixin,
):
    @classmethod
    def get_resource_kind(cls) -> str:
        return sc.SECRET_KIND

    def is_ready_to_actualize(self) -> bool:
        # A secret with neither a value nor a default has nothing to
        # deliver. Hold it in NEW instead of pushing an empty secret to
        # the data plane: an element consuming it waits for the value.
        return self.effective_value is not None


class Password(
    models.Password,
    ua_models.InstanceMixin,
):
    pass

    @classmethod
    def get_resource_kind(cls) -> str:
        return sc.PASSWORD_KIND


class Certificate(
    models.Certificate,
    ua_models.InstanceMixin,
):
    pass

    @classmethod
    def get_resource_kind(cls) -> str:
        return sc.CERTIFICATE_KIND


class RSAKey(
    models.RSAKey,
    ua_models.InstanceMixin,
):
    pass

    @classmethod
    def get_resource_kind(cls) -> str:
        return sc.RSA_KEY_KIND


class SSHKey(
    models.SSHKey,
    ua_models.InstanceMixin,
):
    pass

    @classmethod
    def get_resource_kind(cls) -> str:
        return sc.SSH_KEY_KIND


class SecretBuilder(sdk_builder.UniversalBuilderService):
    def __init__(
        self,
        iter_min_period: int = 1,
        iter_pause: float = 0.1,
    ) -> None:
        super().__init__(
            instance_model=Secret,
            iter_min_period=iter_min_period,
            iter_pause=iter_pause,
        )

    def actualize_outdated_instance(
        self,
        current_instance: Secret,
        actual_instance: Secret,
    ) -> None:
        # The value only travels from the control plane to the data
        # plane, so the status is all there is to bring back.
        if current_instance.status != actual_instance.status:
            current_instance.status = actual_instance.status
            current_instance.save()


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
        status_updated = current_instance.status != actual_instance.status
        if status_updated:
            current_instance.status = actual_instance.status

        password_updated = current_instance.value != actual_instance.value
        if password_updated:
            current_instance.value = actual_instance.value

        if status_updated or password_updated:
            current_instance.save()


class CertificateBuilder(sdk_builder.UniversalBuilderService):
    def __init__(
        self,
        iter_min_period: int = 1,
        iter_pause: float = 0.1,
    ) -> None:
        super().__init__(
            instance_model=Certificate,
            iter_min_period=iter_min_period,
            iter_pause=iter_pause,
        )

    def actualize_outdated_instance(
        self,
        current_instance: Certificate,
        actual_instance: Certificate,
    ) -> None:
        status_updated = current_instance.status != actual_instance.status
        if status_updated:
            current_instance.status = actual_instance.status

        cert_updated = (
            current_instance.key != actual_instance.key
            or current_instance.cert != actual_instance.cert
            or current_instance.expiration_at != actual_instance.expiration_at
        )
        if cert_updated:
            current_instance.key = actual_instance.key
            current_instance.cert = actual_instance.cert
            current_instance.expiration_at = actual_instance.expiration_at

        # Detect domain changes from the DP
        domains_updated = sorted(current_instance.domains) != sorted(
            actual_instance.domains
        )
        if domains_updated:
            current_instance.domains = actual_instance.domains

        if status_updated or cert_updated or domains_updated:
            current_instance.save()
