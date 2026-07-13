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

import datetime
import typing as tp

from gcl_sdk.agents.universal.dm import models as ua_models
from restalchemy.dm import models
from restalchemy.dm import properties
from restalchemy.dm import types
from restalchemy.storage.sql import orm

from exordos_core.common import constants as c
from exordos_core.secret import constants as sc


class Secret(
    models.ModelWithUUID,
    models.ModelWithTimestamp,
):
    status = properties.property(
        types.Enum([s.value for s in sc.SecretStatus]),
        default=sc.SecretStatus.NEW.value,
    )
    # Some additional metadata about the secret
    meta = properties.property(types.Dict(), default=lambda: {})


class Password(Secret, orm.SQLStorableMixin):
    __tablename__ = "storage_passwords"

    value = properties.property(
        types.String(min_length=1, max_length=512),
        required=True,
    )

    @classmethod
    def from_password_resource(
        cls, resource: ua_models.TargetResource, password_value: str
    ) -> "Password":
        meta = resource.value.copy()
        meta["value"] = password_value
        meta["status"] = sc.SecretStatus.ACTIVE.value

        return cls(
            uuid=resource.uuid,
            value=password_value,
            status=sc.SecretStatus.ACTIVE.value,
            meta=meta,
        )


class Certificate(Secret, orm.SQLStorableMixin):
    __tablename__ = "storage_certs"

    pkey = properties.property(
        types.String(min_length=1, max_length=10240),
        required=True,
    )
    fullchain = properties.property(
        types.String(min_length=1, max_length=10240),
        required=True,
    )
    csr = properties.property(
        types.String(min_length=1, max_length=10240),
        required=True,
    )
    expiration_at = properties.property(types.UTCDateTimeZ())

    @classmethod
    def from_cert_resource(
        cls,
        resource: ua_models.TargetResource,
        pkey_pem: bytes,
        csr_pem: bytes,
        fullchain_pem: str,
        expiration_at: datetime.datetime,
    ) -> "Certificate":
        meta = resource.value.copy()
        meta["status"] = sc.SecretStatus.ACTIVE.value

        return cls(
            uuid=resource.uuid,
            pkey=pkey_pem.decode(),
            fullchain=fullchain_pem,
            csr=csr_pem.decode(),
            status=sc.SecretStatus.ACTIVE.value,
            expiration_at=expiration_at,
            meta=meta,
        )

    def is_under_threshold(self) -> bool:
        if self.expiration_at < datetime.datetime.now(tz=datetime.timezone.utc):
            return True
        else:
            delta = self.expiration_at - datetime.datetime.now(tz=datetime.timezone.utc)
            return delta.days < self.meta["expiration_threshold"]

    def to_resource_value(self) -> tp.Dict[str, tp.Any]:
        expiration_at = self.expiration_at.replace(tzinfo=datetime.timezone.utc)
        expiration_at = expiration_at.strftime(c.DEFAULT_DATETIME_FORMAT)

        value = self.meta
        value["status"] = sc.SecretStatus.ACTIVE.value
        value["key"] = self.pkey
        value["cert"] = self.fullchain
        value["expiration_at"] = expiration_at
        value["overcome_threshold"] = self.is_under_threshold()
        return value
