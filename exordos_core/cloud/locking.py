# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0

import contextlib
import functools

from restalchemy.dm import filters
from restalchemy.storage.sql import engines


@contextlib.contextmanager
def provider_lock(connection, blocking=True):
    """Serialize provider side effects without holding a database transaction."""
    engine = engines.engine_factory.get_engine()
    handle = engine.get_connection()
    original = handle.autocommit
    acquired = False
    try:
        handle.autocommit = True
        if blocking:
            handle.execute(
                "SELECT pg_advisory_lock(1129074518, hashtext(%s))", (str(connection),)
            )
            acquired = True
        else:
            acquired = handle.execute(
                "SELECT pg_try_advisory_lock(1129074518, hashtext(%s))",
                (str(connection),),
            ).fetchone()[0]
        yield acquired
    finally:
        if acquired:
            handle.execute(
                "SELECT pg_advisory_unlock(1129074518, hashtext(%s))",
                (str(connection),),
            )
        handle.autocommit = original
        engine.close_connection(handle)


def network_delete_guard(method):
    """Keep deletion serialized with reconciliation until the DB commit."""

    @functools.wraps(method)
    def guarded(self, session=None, **kwargs):
        from exordos_core.compute.dm import models
        from exordos_core.network.contracts import atomic_session

        with atomic_session(self._get_engine(), session) as current:
            network = (
                self
                if isinstance(self, models.Network)
                else models.Network.objects.get_one(
                    filters={"uuid": filters.EQ(self.network)}, session=current
                )
            )
            connection = (
                network.driver_spec.get("connection")
                if network.driver_spec.get("driver") == "cloud"
                else None
            )
            if connection is not None:
                current.execute(
                    "SELECT pg_advisory_xact_lock(1129074518, hashtext(%s))",
                    (str(connection),),
                )
            return method(self, session=current, **kwargs)

    return guarded
