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

from gcl_sdk.agents.universal.api import boot_identity
from gcl_sdk.agents.universal.api import crypto
from gcl_sdk.agents.universal.drivers import pool as ua_pool
from gcl_sdk.agents.universal.status_api import controllers as status_controllers
from oslo_config import cfg
from restalchemy.api import controllers
from restalchemy.api import actions
from restalchemy.api import resources
from restalchemy.dm import filters as dm_filters
from restalchemy.storage import exceptions as ra_storage_exceptions

from exordos_core.boot_api.api import packers
from exordos_core.boot_api.dm import models
from exordos_core.common import constants as c
from exordos_core.compute.dm import models as compute_models

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
        if (
            self._req.environ.get(boot_identity.NODE_ENV) is not None
            and uuid != self._req.context.encryption_information.node_uuid
        ):
            raise packers.BootIdentityError(
                reason="Boot script belongs to another node"
            )
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
        boot_endpoint = CONF[DOMAIN].gc_boot_api
        if self._req.environ.get(boot_identity.NODE_ENV) is not None:
            boot_endpoint = CONF[DOMAIN].proxy_endpoint or boot_endpoint
        netboot.set_netboot_params(
            CONF[DOMAIN].gc_host,
            boot_endpoint,
            CONF[DOMAIN].kernel,
            CONF[DOMAIN].initrd,
        )

        return netboot, 200, {"Content-Type": "application/octet-stream"}


class BootResourcesController(status_controllers.ResourcesController):
    """Provisioning reports have the same node ownership as runtime facts."""


class BootNodesController(status_controllers.NodesController):
    def get(self, uuid, **kwargs):
        if uuid != self._req.context.encryption_information.node_uuid:
            raise packers.BootIdentityError(reason="Boot key belongs to another node")
        return super().get(uuid, **kwargs)

    @actions.post
    def refresh_secret(self, resource):
        # The target machine, not a guest-written status report, authorizes
        # installation. An installed node cannot reset its key through Boot.
        node = self._req.context.encryption_information.node_uuid
        if resource.uuid != node:
            raise packers.BootIdentityError(reason="Boot key belongs to another node")
        machine = compute_models.Machine.objects.get_one(
            filters={"node": dm_filters.EQ(node)}
        )
        if machine.boot != ua_pool.BootAlternative.network.value or not machine.image:
            raise packers.BootIdentityError(reason="Node is not being provisioned")
        _, encoded = crypto.generate_key_base64()
        resource.private_key = encoded
        resource.save()
        return {"key": encoded}


class BootNodeVerifierController(status_controllers.NodeVerifierController):
    def get(self, uuid, **kwargs):
        if uuid != self._req.context.encryption_information.node_uuid:
            raise packers.BootIdentityError(
                reason="Boot verifier belongs to another node"
            )
        return super().get(uuid, **kwargs)
