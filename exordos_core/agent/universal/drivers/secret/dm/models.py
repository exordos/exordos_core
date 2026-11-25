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
import uuid

from gcl_sdk.agents.universal.dm import models as ua_models
from restalchemy.dm import filters
from restalchemy.dm import models
from restalchemy.dm import properties
from restalchemy.dm import types
from restalchemy.storage import exceptions
from restalchemy.storage.sql import orm

from exordos_core.common import constants as c
from exordos_core.common import spiffe
from exordos_core.secret import constants as sc


class AbstractSecret(
    models.ModelWithUUID,
    models.ModelWithTimestamp,
):
    status = properties.property(
        types.Enum([s.value for s in sc.SecretStatus]),
        default=sc.SecretStatus.NEW.value,
    )
    # Some additional metadata about the secret
    meta = properties.property(types.Dict(), default=lambda: {})


class Secret(AbstractSecret, orm.SQLStorableMixin):
    """An opaque secret stored on the data plane."""

    __tablename__ = "storage_secrets"

    value = properties.property(
        types.String(min_length=1, max_length=10240),
        required=True,
    )

    @classmethod
    def from_secret_resource(
        cls, resource: ua_models.TargetResource, value: str
    ) -> "Secret":
        meta = resource.value.copy()
        meta["value"] = value
        meta["status"] = sc.SecretStatus.ACTIVE.value

        return cls(
            uuid=resource.uuid,
            value=value,
            status=sc.SecretStatus.ACTIVE.value,
            meta=meta,
        )


class Password(AbstractSecret, orm.SQLStorableMixin):
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


class Certificate(AbstractSecret, orm.SQLStorableMixin):
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
            if self.meta.get("method", {}).get("kind") == "spiffe":
                # Short-lived SVIDs renew before expiry without changing the hidden
                # legacy threshold used by DNS certificates.
                window = min(
                    datetime.timedelta(days=self.meta["expiration_threshold"]),
                    datetime.timedelta(
                        days=self.meta["method"].get("validity_days", 30)
                    )
                    / 2,
                )
                return delta < window
            return delta.days < self.meta["expiration_threshold"]

    def to_resource_value(self) -> tp.Dict[str, tp.Any]:
        expiration_at = self.expiration_at.replace(tzinfo=datetime.timezone.utc)
        expiration_at = expiration_at.strftime(c.DEFAULT_DATETIME_FORMAT)

        value = self.meta
        value["status"] = sc.SecretStatus.ACTIVE.value
        value["key"] = self.pkey
        value["cert"] = self.fullchain
        value["expiration_at"] = expiration_at
        if value.get("method", {}).get("kind") != "manual":
            value["overcome_threshold"] = self.is_under_threshold()
        return value


class SPIFFEAuthority(models.ModelWithUUID, orm.SQLStorableMixin):
    """Issuer-only material; never exported as a target resource or secret value."""

    __tablename__ = "storage_spiffe_authorities"

    project_id = properties.property(types.UUID(), required=True)
    trust_domain = properties.property(
        types.String(min_length=1, max_length=255), required=True
    )
    key = properties.property(
        types.String(min_length=1, max_length=10240), required=True
    )
    cert = properties.property(
        types.String(min_length=1, max_length=10240), required=True
    )


    @classmethod
    def get_or_create(cls, project: uuid.UUID, domain: str):
        authority_id = uuid.uuid5(project, f"spiffe://{domain}")
        try:
            return cls.objects.get_one(filters={"uuid": filters.EQ(authority_id)})
        except exceptions.RecordNotFound:
            issued = spiffe.create_authority(domain)
            authority = cls(
                uuid=authority_id,
                project_id=project,
                trust_domain=domain,
                key=issued.key.decode(),
                cert=issued.cert,
            )
            # An INSERT conflict retries through the caller without replacing the CA.
            authority.insert()
            return authority
