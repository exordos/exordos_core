# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0

import functools

from gcl_sdk.agents.universal.dm import models as ua
from gcl_sdk.cloud.bootstrap import validate_agent_config
from gcl_sdk.cloud.drivers import base
from restalchemy.dm import filters
from restalchemy.dm import properties
from restalchemy.dm import types
from restalchemy.storage import exceptions
from restalchemy.storage.sql import orm

from exordos_core.common.dm import models
from exordos_core.common.exceptions import ValidateException


def connection_guard(identity):
    """Serialize connection lifecycle and writes to its dependent resources."""

    def decorate(method):
        @functools.wraps(method)
        def guarded(self, session=None, **kwargs):
            from exordos_core.network.contracts import atomic_session

            reference = identity(self)
            if reference is None:
                return method(self, session=session, **kwargs)
            with atomic_session(self._get_engine(), session) as current:
                current.execute(
                    "SELECT pg_advisory_xact_lock(1129074517, hashtext(%s))",
                    (str(reference),),
                )
                return method(self, session=current, **kwargs)

        return guarded

    return decorate


class CloudConnection(
    models.ModelWithFullAsset, orm.SQLStorableMixin, ua.ResourceMixin
):
    """Operator-owned reference to credentials held in local clouds.yaml."""

    __tablename__ = "cloud_connections"

    cloud = properties.property(
        types.String(min_length=1, max_length=255), required=True
    )
    driver = properties.property(
        types.String(min_length=1, max_length=255), default="openstack"
    )
    region = properties.property(
        types.AllowNone(types.String(max_length=255)), default=None
    )
    orch_endpoint = properties.property(types.String(max_length=2048), required=True)
    status_endpoint = properties.property(types.String(max_length=2048), required=True)
    agent_package = properties.property(
        types.String(min_length=1, max_length=2048), required=True
    )

    def validate_bootstrap(self):
        try:
            validate_agent_config(
                self.orch_endpoint, self.status_endpoint, self.agent_package
            )
        except ValueError as error:
            raise ValidateException(err=str(error)) from error

    def insert(self, session=None):
        self.validate_bootstrap()
        return super().insert(session=session)

    def load_driver(self):
        return base.load_driver(
            base.CloudConfig(
                connection=self.uuid,
                cloud=self.cloud,
                driver=self.driver,
                region=self.region,
            )
        )

    @connection_guard(lambda self: self.uuid)
    def update(self, session=None, force=False):
        self.validate_bootstrap()
        stored = self.objects.get_one(
            filters={"uuid": filters.EQ(self.uuid)}, session=session
        )
        if any(
            getattr(stored, f) != getattr(self, f)
            for f in (
                "cloud",
                "driver",
                "region",
                "project_id",
                "orch_endpoint",
                "status_endpoint",
                "agent_package",
            )
        ):
            raise exceptions.ConflictRecords(
                model="CloudConnection", msg="cloud scope and bootstrap are immutable"
            )
        return super().update(session=session, force=force)

    @connection_guard(lambda self: self.uuid)
    def delete(self, session=None, **kwargs):
        from exordos_core.cloud.links import CloudLinkEndpoint
        from exordos_core.compute.dm import models as compute

        used = bool(
            CloudLinkEndpoint.objects.get_all(
                filters={"connection": filters.EQ(self.uuid)}, session=session
            )
        )
        used |= any(
            str(n.driver_spec.get("connection")) == str(self.uuid)
            for n in compute.Network.objects.get_all(session=session)
        )
        used |= any(
            getattr(p.driver_spec, "connection", None) == self.uuid
            for p in compute.MachinePool.objects.get_all(session=session)
        )
        if used:
            raise exceptions.ConflictRecords(
                model="CloudConnection", msg="connection is in use"
            )
        return super().delete(session=session, **kwargs)


def connection(uuid, project_id=None, session=None):
    query = {"uuid": filters.EQ(uuid)}
    if project_id is not None:
        query["project_id"] = filters.EQ(project_id)
    return CloudConnection.objects.get_one(filters=query, session=session)


def bind_pool(pool, session=None):
    spec = pool.driver_spec
    if spec.KIND == "cloud":
        config = connection(spec.connection, session=session)
        spec.cloud, spec.driver, spec.region = (
            config.cloud,
            config.driver,
            config.region,
        )
