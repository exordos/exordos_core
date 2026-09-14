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

from gcl_sdk.agents.universal.clients.backend import base
from gcl_sdk.agents.universal.clients.backend import exceptions
from gcl_sdk.agents.universal.dm import models
from restalchemy.dm import filters as dm_filters
from restalchemy.storage import exceptions as ra_exc

from exordos_core.agent.universal.drivers.secret.dm import models as driver_dm
from exordos_core.secret.dm import models as secret_dm

LOG = logging.getLogger(__name__)


class DatabaseSecretBackendClient(base.AbstractBackendClient):
    """Opaque secret backend client based on SQL database."""

    def get(self, resource: models.Resource) -> tp.Dict[str, tp.Any]:
        """Get the resource value in dictionary format."""
        try:
            driver_secret = driver_dm.Secret.objects.get_one(
                filters={
                    "uuid": dm_filters.EQ(resource.uuid),
                },
            )
        except ra_exc.RecordNotFound:
            raise exceptions.ResourceNotFound(resource=resource)

        return driver_secret.meta

    def create(self, resource: models.Resource) -> tp.Dict[str, tp.Any]:
        """Creates the resource. Returns the created resource."""
        try:
            self.get(resource)
        except exceptions.ResourceNotFound:
            pass
        else:
            raise exceptions.ResourceAlreadyExists(resource=resource)

        secret = secret_dm.Secret.from_ua_resource(resource)

        # Build the secret from the plain view
        value = secret.constructor.build(secret.value)

        driver_secret = driver_dm.Secret.from_secret_resource(resource, value)
        driver_secret.save()
        return driver_secret.meta

    def update(self, resource: models.Resource) -> tp.Dict[str, tp.Any]:
        """Update the resource. Returns the updated resource."""
        target = secret_dm.Secret.from_ua_resource(resource)
        actual = driver_dm.Secret.objects.get_one(
            filters={
                "uuid": dm_filters.EQ(resource.uuid),
            }
        )

        value = target.constructor.build(target.value)

        # Rebuild the whole meta, not just the value: every target field
        # lives in it and the agent compares its hash against the target
        # resource, so a stale field never converges.
        new = driver_dm.Secret.from_secret_resource(resource, value)

        if new.value != actual.value or new.meta != actual.meta:
            actual.value = new.value
            actual.meta = new.meta
            actual.save()

        return actual.meta

    def list(self, kind: str, **kwargs) -> tp.List[tp.Dict[str, tp.Any]]:
        """Lists all resources by kind."""
        secrets = driver_dm.Secret.objects.get_all()
        return [s.meta for s in secrets]

    def delete(self, resource: models.Resource) -> None:
        """Delete the resource."""
        try:
            self.get(resource)
        except exceptions.ResourceNotFound:
            raise exceptions.ResourceNotFound(resource=resource)

        secret = driver_dm.Secret.objects.get_one(
            filters={
                "uuid": dm_filters.EQ(resource.uuid),
            }
        )
        secret.delete()
