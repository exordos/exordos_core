# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0

import functools

from gcl_sdk.agents.universal.dm import models as ua
from restalchemy.dm import filters
from restalchemy.dm import properties
from restalchemy.dm import types
from restalchemy.dm import types_dynamic
from restalchemy.storage import exceptions
from restalchemy.storage.sql import orm

from exordos_core.cloud import specs
from exordos_core.common.dm import models


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
    models.ModelWithFullAsset, orm.SQLStorableWithJSONFieldsMixin, ua.ResourceMixin
):
    """Project-scoped provider credentials retained on the controller."""

    __tablename__ = "cloud_connections"

    __jsonfields__ = ["driver_state"]
    driver_spec = properties.property(
        types_dynamic.KindModelSelectorType(
            types_dynamic.KindModelType(specs.OpenStackConnectionSpec)
        ),
        required=True,
    )
    driver_state = properties.property(types.Dict(), default=dict)

    def load_driver(self):
        return self.driver_spec.load_driver(self.uuid)

    def _authenticate(self):
        return self.driver_spec.authenticate(self.uuid)

    @connection_guard(lambda self: self.uuid)
    def insert(self, session=None):
        self.driver_spec.validate_config(self.uuid)
        self.driver_state = self._authenticate()
        return super().insert(session=session)

    @connection_guard(lambda self: self.uuid)
    def update(self, session=None, force=False):
        self.driver_spec.validate_config(self.uuid)
        stored = self.objects.get_one(
            filters={"uuid": filters.EQ(self.uuid)}, session=session
        )
        if (
            self.project_id != stored.project_id
            or self.driver_state != stored.driver_state
            or self.driver_spec.public_view() != stored.driver_spec.public_view()
        ):
            raise exceptions.ConflictRecords(
                model="CloudConnection", msg="cloud scope is immutable"
            )
        if (
            self.driver_spec.dump_to_simple_view()
            != stored.driver_spec.dump_to_simple_view()
            and self._authenticate() != stored.driver_state
        ):
            raise exceptions.ConflictRecords(
                model="CloudConnection",
                msg="credential belongs to a different provider scope",
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
        return config
