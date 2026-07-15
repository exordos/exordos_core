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

from gcl_sdk.agents.universal.dm import models as ua_models
from restalchemy.storage import exceptions as storage_exceptions
from restalchemy.storage.sql import orm as sql_orm

from exordos_core.agent.universal.drivers.secret.dm import (
    models as secret_driver_models,
)
from exordos_core.secret.dm import models as secret_models

INTERNAL_IAM_CLIENT_SECRET_UUID = sys_uuid.UUID("00000000-0000-0000-0000-000000000001")

INTERNAL_IAM_CLIENT_SECRET_UUID_STR = str(INTERNAL_IAM_CLIENT_SECRET_UUID)


class ObjectCollection(sql_orm.ObjectCollection):
    def get_all(self, *args, **kwargs):
        objects = super().get_all(*args, **kwargs)

        if args or kwargs:
            return objects

        internal_uuid_str = str(INTERNAL_IAM_CLIENT_SECRET_UUID)
        return [o for o in objects if str(getattr(o, "uuid", "")) != internal_uuid_str]

    def get_one(self, *args, **kwargs):
        if args or kwargs:
            return super().get_one(*args, **kwargs)

        objects = self.get_all()
        if not objects:
            return None
        if len(objects) > 1:
            raise storage_exceptions.HasManyRecords(
                "Has many records in storage for model (%s) and filters (%s)."
                % (self.model_cls, None)
            )
        return objects[0]


class TargetResource(ua_models.TargetResource):
    _ObjectCollection = ObjectCollection


class Resource(ua_models.Resource):
    _ObjectCollection = ObjectCollection


class Password(secret_models.Password):
    _ObjectCollection = ObjectCollection


class StoragePassword(secret_driver_models.Password):
    _ObjectCollection = ObjectCollection


class Certificate(secret_models.Certificate):
    _ObjectCollection = ObjectCollection


class StorageCertificate(secret_driver_models.Certificate):
    _ObjectCollection = ObjectCollection
