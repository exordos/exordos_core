# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0

import uuid

from gcl_iam.api import controllers
from restalchemy.api import actions
from restalchemy.api import constants
from restalchemy.api import field_permissions
from restalchemy.api import resources
from restalchemy.api import routes

from exordos_core.cloud import models
from exordos_core.cloud.links import CloudNetworkLink
from exordos_core.common.exceptions import ValidateException


class CloudConnectionsController(controllers.PolicyBasedController):
    __policy_name__ = "hypervisor"
    __policy_service_name__ = "compute"
    __resource__ = resources.ResourceByRAModel(
        models.CloudConnection, convert_underscore=False
    )


class CloudConnectionsRoute(routes.Route):
    __controller__ = CloudConnectionsController


class CloudNetworkLinksController(controllers.PolicyBasedController):
    __policy_name__ = "hypervisor"
    __policy_service_name__ = "compute"
    __resource__ = resources.ResourceByRAModel(
        CloudNetworkLink,
        convert_underscore=False,
        fields_permissions=field_permissions.FieldsPermissions(
            default=field_permissions.Permissions.RW,
            fields={
                "secrets": {constants.ALL: field_permissions.Permissions.HIDDEN},
                "state": {constants.ALL: field_permissions.Permissions.RO},
                "recovery": {constants.ALL: field_permissions.Permissions.RO},
                "status": {constants.ALL: field_permissions.Permissions.RO},
            },
        ),
    )

    @actions.post
    def recover_gateway(self, resource: CloudNetworkLink, subnet: str):
        self._enforce("update")
        try:
            subnet_id = uuid.UUID(subnet)
        except (TypeError, ValueError) as error:
            raise ValidateException(err="A subnet UUID is required") from error
        return resource.request_recovery(subnet_id)


class RecoverGatewayAction(routes.Action):
    __controller__ = CloudNetworkLinksController


class CloudNetworkLinksRoute(routes.Route):
    __controller__ = CloudNetworkLinksController
    recover_gateway = routes.action(RecoverGatewayAction, invoke=True)
