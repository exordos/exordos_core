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

from gcl_sdk.agents.universal.api import controllers as sdk_controllers
from gcl_sdk.agents.universal.drivers import pool as ua_pool
from gcl_sdk.agents.universal.status_api import controllers as status_controllers
from gcl_sdk.agents.universal.status_api.dm import models as status_models
from oslo_config import cfg
from restalchemy.api import controllers
from restalchemy.api import resources
from restalchemy.dm import filters as dm_filters
from restalchemy.storage import exceptions as ra_storage_exceptions

from exordos_core.boot_api.api import packers
from exordos_core.boot_api.dm import models
from exordos_core.common import constants as c

DOMAIN = "boot_api"
CONF = cfg.CONF


class ApiEndpointController(controllers.RoutesListController):
    """Controller for /v1/ endpoint"""

    __TARGET_PATH__ = "/v1/"


class NetBootController(controllers.BaseResourceController):
    """Controller for /v1/boots/ endpoint"""

    __resource__ = resources.ResourceByRAModel(
        model_class=models.MachineNetboot,
        process_filters=True,
        convert_underscore=False,
    )

    __packer__ = packers.IPXEPacker

    def get(self, uuid, **kwargs):
        try:
            netboot = super().get(uuid, **kwargs)
        except ra_storage_exceptions.RecordNotFound:
            # Generate a dummy netboot object for netboot
            # configuration. Network is default option
            # for such machines.
            netboot = models.MachineNetboot(
                uuid=uuid,
                cores=0,
                ram=0,
                boot=ua_pool.BootAlternative.network.value,
                project_id=c.ZERO_UUID,
            )

        # Set netboot configuration
        netboot.set_netboot_params(
            CONF[DOMAIN].gc_host,
            CONF[DOMAIN].gc_boot_api,
            CONF[DOMAIN].kernel,
            CONF[DOMAIN].initrd,
        )

        return netboot, 200, {"Content-Type": "application/octet-stream"}


class BootResourcesController(status_controllers.ResourcesController):
    """Keep Seed's guest-machine provisioning on the isolated Boot network."""

    def create(self, parent_resource, **kwargs):
        if parent_resource.kind == "guest_machine":
            return sdk_controllers.BaseSdkNestedResourceController.create(
                self, parent_resource=parent_resource, **kwargs
            )
        return super().create(parent_resource=parent_resource, **kwargs)

    def filter(self, parent_resource, filters, order_by=None):
        if parent_resource.kind == "guest_machine":
            return status_models.Resource.objects.get_all(
                filters={
                    **(filters or {}),
                    "kind": dm_filters.EQ(parent_resource.kind),
                }
            )
        return super().filter(parent_resource, filters, order_by)

    def get(self, uuid, parent_resource, **kwargs):
        if parent_resource.kind == "guest_machine":
            return status_models.Resource.objects.get_one(
                filters={
                    "kind": dm_filters.EQ(parent_resource.kind),
                    "uuid": dm_filters.EQ(str(uuid)),
                }
            )
        return super().get(uuid, parent_resource, **kwargs)

    def update(self, parent_resource, uuid, **kwargs):
        if parent_resource.kind == "guest_machine":
            kwargs["kind"] = parent_resource
            return sdk_controllers.BaseSdkNestedResourceController.update(
                self, parent_resource=parent_resource, uuid=uuid, **kwargs
            )
        return super().update(parent_resource=parent_resource, uuid=uuid, **kwargs)
