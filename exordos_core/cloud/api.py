# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0

import time
import uuid

from gcl_iam.api import controllers
from restalchemy.api import actions
from restalchemy.api import constants
from restalchemy.api import field_permissions
from restalchemy.api import packers
from restalchemy.api import resources
from restalchemy.api import routes
from restalchemy.api.middlewares import logging as logging_mw
from restalchemy.common import exceptions as ra_exceptions

from exordos_core.cloud.links import CloudNetworkLink
from exordos_core.common.exceptions import ValidateException


class HypervisorCredentialsLoggingMiddleware(logging_mw.LoggingMiddleware):
    def process_request(self, req):
        path = req.path_info
        if not any(
            path.rstrip("/") == prefix or path.startswith(prefix + "/")
            for prefix in (
                "/v1/compute/hypervisors",
                "/v1/compute/nodes",
                "/v1/config/configs",
            )
        ):
            return super().process_request(req)
        started = time.perf_counter()
        status = 500
        try:
            response = req.get_response(self.application)
            status = response.status_int
            return response
        finally:
            self.logger.info(
                "%s %s %s %.3fs",
                req.method,
                path,
                status,
                time.perf_counter() - started,
            )


class HypervisorPacker(packers.JSONPacker):
    def pack_resource(self, obj):
        value = super().pack_resource(obj)
        if hasattr(obj.driver_spec, "public_view"):
            value["driver_spec"] = obj.driver_spec.public_view()
        return value

    def unpack(self, value):
        try:
            return super().unpack(value)
        except (
            ra_exceptions.ParseError,
            ra_exceptions.ValidationErrorException,
            ra_exceptions.TypeError,
            ra_exceptions.PropertyException,
        ):
            raise ValidateException(
                err="Invalid hypervisor fields or driver kind"
            ) from None


class NodeBootstrapPacker(packers.JSONPacker):
    def pack_resource(self, obj):
        value = super().pack_resource(obj)
        value.pop("bootstrap", None)
        value.pop("ports", None)
        return value

    def unpack(self, value):
        try:
            return super().unpack(value)
        except (
            ra_exceptions.ParseError,
            ra_exceptions.ValidationErrorException,
            ra_exceptions.TypeError,
            ra_exceptions.PropertyException,
        ):
            raise ValidateException(err="Invalid node or bootstrap fields") from None


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
                "owner_network": {constants.ALL: field_permissions.Permissions.RO},
            },
        ),
    )

    def update(self, uuid, **kwargs):
        resource = self.get(uuid=uuid)
        if resource.owner_network is not None:
            raise ValidateException(
                err="manage connectivity through its logical network"
            )
        return super().update(uuid=uuid, **kwargs)

    def delete(self, uuid):
        resource = self.get(uuid=uuid)
        if resource.owner_network is not None:
            raise ValidateException(
                err="manage connectivity through its logical network"
            )
        return super().delete(uuid=uuid)

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
