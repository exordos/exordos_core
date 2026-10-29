# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0

"""Resolve provider operations through their owning hypervisor."""

import functools

from restalchemy.dm import filters

from exordos_core.common.exceptions import ValidateException


def connection_guard(identity):
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


def connection(uuid, project_id=None, session=None):
    from exordos_core.compute.dm import models

    query = {"uuid": filters.EQ(uuid)}
    if project_id is not None:
        query["project_id"] = filters.EQ(project_id)
    hypervisor = models.MachinePool.objects.get_one(filters=query, session=session)
    if not hypervisor.is_cloud:
        raise ValidateException(err="A cloud hypervisor is required")
    return hypervisor
