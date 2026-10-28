# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0

from gcl_sdk.cloud.drivers import base
from restalchemy.dm import models
from restalchemy.dm import properties
from restalchemy.dm import types
from restalchemy.dm import types_dynamic

from exordos_core.common.exceptions import ValidateException


class AbstractConnectionSpec(types_dynamic.AbstractKindModel, models.SimpleViewMixin):
    SENSITIVE_FIELDS = frozenset()

    def public_view(self):
        value = self.dump_to_simple_view()
        return {
            key: item for key, item in value.items() if key not in self.SENSITIVE_FIELDS
        }

    def validate_config(self, connection):
        raise NotImplementedError

    def load_driver(self, connection):
        raise NotImplementedError

    def authenticate(self, connection):
        raise NotImplementedError


class OpenStackConnectionSpec(AbstractConnectionSpec):
    KIND = "openstack"
    SENSITIVE_FIELDS = frozenset({"auth"})

    auth_url = properties.property(
        types.String(min_length=1, max_length=2048), required=True
    )
    auth_type = properties.property(
        types.Enum(["v3applicationcredential", "v3password"]),
        default="v3applicationcredential",
    )
    auth = properties.property(types.Dict(), required=True)
    region = properties.property(
        types.AllowNone(types.String(max_length=255)), default=None
    )
    interface = properties.property(
        types.Enum(["public", "internal", "admin"]), default="public"
    )

    def driver_config(self, connection):
        try:
            return base.CloudConfig(
                connection=connection,
                auth_url=self.auth_url,
                auth=self.auth,
                auth_type=self.auth_type,
                region=self.region,
                interface=self.interface,
            )
        except ValueError as error:
            raise ValidateException(err=str(error)) from None

    def validate_config(self, connection):
        self.driver_config(connection)

    def load_driver(self, connection):
        return base.load_driver(self.driver_config(connection))

    def authenticate(self, connection):
        try:
            driver = self.load_driver(connection)
            driver.api.authorize()
            project = driver.api.current_project_id
            if not isinstance(project, str) or not project:
                raise ValueError("A project-scoped credential is required")
            return {"project_id": project}
        except Exception:
            raise ValidateException(
                err="Unable to authenticate a project-scoped OpenStack credential"
            ) from None
