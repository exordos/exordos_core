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

import re
import uuid as sys_uuid

from gcl_sdk.agents.universal.dm import models as ua_models
from oslo_config import cfg
from restalchemy.common import exceptions
from restalchemy.dm import models
from restalchemy.dm import properties
from restalchemy.dm import relationships
from restalchemy.dm import types
from restalchemy.dm import types_dynamic
from restalchemy.dm import types_network
from restalchemy.storage.sql import orm

from exordos_core.common import utils as u

CONF = cfg.CONF


class SOARecordDeleteRestricted(exceptions.RestAlchemyException):
    code = 403
    message = "SOA record cannot be deleted without domain deletion."


class CommonModel(
    models.ModelWithTimestamp,
    models.ModelWithUUID,
    orm.SQLStorableMixin,
    models.SimpleViewMixin,
):
    pass


class Domain(CommonModel, models.ModelWithProject, ua_models.TargetResourceMixin):
    __tablename__ = "dns_domains"
    name = properties.property(types.String(), required=True)
    sync_to_ecosystem = properties.property(types.Boolean(), default=False)
    # Used only for PDNS
    id = properties.property(types.Integer())
    # Next columns exist in DB but used only for PDNS support and have
    #  sane defaults.
    # id = properties.property(types.Integer())
    # last_check = properties.property(types.Integer(), default=None)
    # notified_serial = properties.property(types.Integer(), default=None)
    # type = properties.property(types.Enum(("PRIMARY", "SLAVE")), required=True)
    # master = properties.property(types.String(), default=None)
    # account = properties.property(types.String(), default=None)
    # options = properties.property(types.Text(), default=None)
    # catalog = properties.property(types.Text(), default=None)

    @classmethod
    def get_next_domain_id(cls, session=None):
        with cls._get_engine().session_manager(session=session) as s:
            return s.execute("SELECT nextval('dns_domain_id_seq') as val").fetchall()[
                0
            ]["val"]

    def __init__(self, session=None, **kwargs):
        super().__init__(id=self.get_next_domain_id(session=session), **kwargs)

    def insert(self, session=None):
        # TODO: to be public autoritative DNS, we need:
        #  - make sure the SOA record is correct (serial, too, for zone transfers)
        #    (or don't update serial, it's needed only for secondary DNS replicaion,
        #     we can just don't support it, route53 doesn't support it either)
        super().insert(session=session)
        # TODO: make default soa record configurable
        # TODO: set soa serial as date, see ya.ru for example
        soa = Record(
            uuid=sys_uuid.uuid5(self.uuid, "soa"),
            domain=self,
            project_id=self.project_id,
            type="SOA",
            record=SOARecord(
                name="",
            ),
        )
        soa.save(session=session)

    def delete(self, session=None, **kwargs):
        u.remove_nested_dm(Record, "domain", self, session=session, force=True)
        return super().delete(session=session, **kwargs)


# TODO: configure powerdns to not even try to read domainmetadata?
# NOTE: Powerdns checks settings per each domain, non-existent row is ok too,
#  so just don't implement it if not needed.
# class DomainMetadata:
#    __tablename__ = "domainmetadata"


class AbstractRecord(types_dynamic.AbstractKindModel):
    def get_name(self, domain) -> str:
        return (".").join((self.name, domain.name)) if self.name else domain.name

    def get_content(self, domain) -> str:
        return str(self.content)


class ARecord(AbstractRecord):
    KIND = "A"

    name = properties.property(
        types_network.RecordNameWithWildcard(),
        required=True,
    )
    address = properties.property(
        types_network.IPAddress(),
        required=True,
    )

    def get_content(self, domain) -> str:
        return str(self.address)


class SOARecord(AbstractRecord):
    KIND = "SOA"

    name = properties.property(
        types_network.RecordNameWithWildcard(),
        required=True,
    )
    primary_dns = properties.property(
        types_network.Hostname(), default="a.misconfigured.dns.server.invalid"
    )
    # serial may not be incremented if we don't need domain transfers
    serial = properties.property(types.Integer(min_value=0), default=0)
    refresh = properties.property(types.Integer(min_value=60), default=10800)
    retry = properties.property(types.Integer(min_value=60), default=3600)
    expire = properties.property(types.Integer(min_value=60), default=604800)
    ttl = properties.property(types.Integer(min_value=60), default=3600)

    def get_content(self, domain) -> str:
        return f"{self.primary_dns} {domain.name} {self.serial} {self.refresh} {self.retry} {self.expire} {self.ttl}"


class TXTRecord(AbstractRecord):
    KIND = "TXT"

    name = properties.property(
        types_network.RecordNameWithWildcard(),
        required=True,
    )
    content = properties.property(
        # Restrict newline and length
        types.BaseCompiledRegExpType(re.compile(r"^([^\n]{1,8192})$")),
        required=True,
    )


class NSRecord(AbstractRecord):
    KIND = "NS"

    name = properties.property(
        types_network.RecordNameWithWildcard(),
        required=True,
    )
    content = properties.property(
        types_network.FQDN(),
        required=True,
    )


class Record(CommonModel, models.ModelWithProject, ua_models.TargetResourceMixin):
    __tablename__ = "dns_records"
    domain = relationships.relationship(Domain, required=True)
    domain_id = properties.property(types.Integer())
    type = properties.property(
        types.Enum(("A", "NS", "SOA", "TXT")),  # "AAAA", "CNAME", "MX",
        read_only=True,
        required=True,
    )
    ttl = properties.property(types.Integer(), required=True, default=3600)
    prio = properties.property(types.Integer(), default=None)
    disabled = properties.property(types.Boolean(), default=False)
    record = properties.property(
        types_dynamic.KindModelSelectorType(
            types_dynamic.KindModelType(ARecord),
            types_dynamic.KindModelType(SOARecord),
            types_dynamic.KindModelType(TXTRecord),
            types_dynamic.KindModelType(NSRecord),
        ),
        required=True,
    )
    # Next columns are autofilled with record submodel's data for powerdns
    name = properties.property(types.String())
    content = properties.property(types.String())
    # Next columns exist in DB but used only for PDNS support and have
    #  sane defaults.
    # domain_id = properties.property(types.Integer())
    # ordername = properties.property(types.String(), default=None)
    # auth = properties.property(types.Boolean(), default=True)С

    def __init__(self, domain: Domain, **kwargs) -> None:
        super().__init__(domain=domain, domain_id=domain.id, **kwargs)

        self._fill_n_validate_record()

    def _fill_n_validate_record(self) -> None:
        if self.type != self.record.kind:
            raise ValueError("Types of model and record must match")

        self.content = self.record.get_content(self.domain)
        self.name = self.record.get_name(self.domain)

    def update(self, session=None, force=False):
        self._fill_n_validate_record()

        super().update(session=session, force=force)

    def insert(self, session=None):
        self._fill_n_validate_record()

        super().insert(session=session)

    def delete(self, session=None, force=False, **kwargs):
        if not force and self.type == "SOA":
            raise SOARecordDeleteRestricted()
        return super().delete(session=session, **kwargs)
