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

from gcl_iam.api import controllers as iam_controllers
from oslo_config import cfg
from restalchemy.api import constants
from restalchemy.api import controllers as ra_controllers
from restalchemy.api import field_permissions as field_p
from restalchemy.api import resources

from exordos_core.user_api.network.dm import models

CONF = cfg.CONF


class NetworkController(ra_controllers.RoutesListController):
    __TARGET_PATH__ = "/v1/network/"


class LBController(
    iam_controllers.PolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    __policy_service_name__ = "network"
    __policy_name__ = "lb"

    __resource__ = resources.ResourceByRAModel(
        models.LB,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "status": {constants.ALL: field_p.Permissions.RO},
                "ipsv4": {constants.ALL: field_p.Permissions.RO},
            },
        ),
    )


class VhostController(
    iam_controllers.NestedPolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    __pr_name__ = "parent"
    __policy_service_name__ = "network"
    __policy_name__ = "lb_vhost"

    __resource__ = resources.ResourceByRAModel(
        models.Vhost,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "status": {constants.ALL: field_p.Permissions.RO},
                "parent": {constants.ALL: field_p.Permissions.HIDDEN},
            },
        ),
    )


class VhostRouteController(
    iam_controllers.NestedPolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    __pr_name__ = "parent"
    __policy_service_name__ = "network"
    __policy_name__ = "lb_vhost_route"

    __resource__ = resources.ResourceByRAModel(
        models.Route,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "status": {constants.ALL: field_p.Permissions.RO},
                "parent": {constants.ALL: field_p.Permissions.HIDDEN},
            },
        ),
    )


class BackendPoolController(
    iam_controllers.NestedPolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    __pr_name__ = "parent"
    __policy_service_name__ = "network"
    __policy_name__ = "lb_backendpool"

    __resource__ = resources.ResourceByRAModel(
        models.BackendPool,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "status": {constants.ALL: field_p.Permissions.RO},
                "parent": {constants.ALL: field_p.Permissions.HIDDEN},
            },
        ),
    )


class BorderController(
    iam_controllers.PolicyBasedController,
    ra_controllers.BaseResourceControllerPaginated,
):
    __policy_service_name__ = "network"
    __policy_name__ = "border"

    __resource__ = resources.ResourceByRAModel(
        models.Border,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "status": {constants.ALL: field_p.Permissions.RO},
                "ipsv4": {constants.ALL: field_p.Permissions.RO},
            },
        ),
    )
