#    Copyright 2026 Genesis Corporation.
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
import typing as tp
import uuid as sys_uuid

from restalchemy.common import exceptions as ra_e
from restalchemy.dm import filters as dm_filters
from restalchemy.dm import models
from restalchemy.dm import properties
from restalchemy.dm import types
from restalchemy.storage.sql import orm

LOG = logging.getLogger(__name__)


class QuotaExceededError(ra_e.ValidationErrorException):
    message = "Quota exceeded for resource '%(resource_name)s' in project %(project_id)s: %(current)s > %(limit)s"

    def __init__(
        self, resource_name: str, limit: int, current: int, project_id: sys_uuid.UUID
    ):
        super().__init__(
            resource_name=resource_name,
            limit=limit,
            current=current,
            project_id=project_id,
        )
        self.resource_name = resource_name
        self.limit = limit
        self.current = current
        self.project_id = project_id


DEFAULT_QUOTA_LIMIT = 1000
DEFAULT_QUOTA_LIMITS: tp.Dict[str, int] = {
    "net_lb": DEFAULT_QUOTA_LIMIT,
    "compute_sets": DEFAULT_QUOTA_LIMIT,
    "nodes": DEFAULT_QUOTA_LIMIT,
    "secret_passwords": DEFAULT_QUOTA_LIMIT,
    "secret_certificates": DEFAULT_QUOTA_LIMIT,
    "secret_rsa_keys": DEFAULT_QUOTA_LIMIT,
    "secret_ssh_keys": DEFAULT_QUOTA_LIMIT,
}
DEFAULT_QUOTA_FIELD_LIMITS: tp.Dict[str, tp.Dict[str, int]] = {
    "nodes": {"cores": 10000},
}


class QuotaModelMixin:
    def _quota_limits(self, session) -> tp.Collection["QuotaLimit"]:
        limits = list(
            QuotaLimit.objects.get_all(
                session=session,
                filters={
                    "project_id": dm_filters.EQ(self.project_id),
                    "resource_name": dm_filters.EQ(self.__tablename__),
                },
            )
        )
        limit_fields = {limit.field_name for limit in limits}
        if "" not in limit_fields:
            default_limit = DEFAULT_QUOTA_LIMITS.get(self.__tablename__)
            if default_limit is not None:
                limits.append(
                    QuotaLimit(
                        project_id=self.project_id,
                        resource_name=self.__tablename__,
                        field_name="",
                        limit=default_limit,
                    )
                )

        for field_name, default_limit in DEFAULT_QUOTA_FIELD_LIMITS.get(
            self.__tablename__, {}
        ).items():
            if field_name not in limit_fields:
                limits.append(
                    QuotaLimit(
                        project_id=self.project_id,
                        resource_name=self.__tablename__,
                        field_name=field_name,
                        limit=default_limit,
                    )
                )
        return limits

    def _quota_check(self, session) -> None:
        # Check entity-count and aggregate-field limits before inserting.
        limits = self._quota_limits(session)
        if not limits:
            return

        filters = {"project_id": dm_filters.EQ(self.project_id)}
        field_limits = [limit for limit in limits if limit.field_name]
        count_limits = [limit for limit in limits if not limit.field_name]

        if field_limits:
            field_names = {limit.field_name for limit in field_limits}
            invalid_field_names = field_names - set(self.properties.properties)
            if invalid_field_names:
                raise ValueError(
                    f"Unknown quota field(s): {', '.join(sorted(invalid_field_names))}"
                )

            fields = ", ".join(sorted(field_names))
            result = session.execute(
                f"SELECT {fields} FROM {self.__tablename__} WHERE project_id = %s",
                (self.project_id,),
            )
            rows = result.fetchall()
            for quota_limit in field_limits:
                current = sum(row[quota_limit.field_name] for row in rows) + getattr(
                    self, quota_limit.field_name
                )
                if current > quota_limit.limit:
                    raise QuotaExceededError(
                        resource_name=f"{self.__tablename__}.{quota_limit.field_name}",
                        limit=quota_limit.limit,
                        current=current,
                        project_id=self.project_id,
                    )

        if count_limits:
            current = self.objects.count(session=session, filters=filters) + 1
            for quota_limit in count_limits:
                if current > quota_limit.limit:
                    raise QuotaExceededError(
                        resource_name=self.__tablename__,
                        limit=quota_limit.limit,
                        current=current,
                        project_id=self.project_id,
                    )

    def insert(self, session=None):
        # Reserve quota capacity by checking entity-count and field totals.
        if session is None:
            with self._get_engine().session_manager(session=session) as s:
                self._quota_check(s)
                super().insert(session=s)
        else:
            self._quota_check(session)
            super().insert(session=session)


class QuotaLimit(
    models.ModelWithUUID,
    models.ModelWithTimestamp,
    models.ModelWithProject,
    orm.SQLStorableMixin,
):
    __tablename__ = "quota_limits"

    resource_name = properties.property(
        types.String(max_length=255),
        required=True,
    )
    field_name = properties.property(
        types.String(max_length=255),
        default="",
    )
    limit = properties.property(
        types.Integer(min_value=0),
        required=True,
    )
