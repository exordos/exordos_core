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

import base64
import uuid

from cryptography.exceptions import InvalidTag
from gcl_sdk.agents.universal.api import boot_identity
from gcl_sdk.agents.universal.dm import models as ua_models
from restalchemy.dm import filters
from restalchemy.storage import exceptions as storage_exc
import webob

from exordos_core.boot_api.api import packers
from exordos_core.compute.dm import models


class BootProxyMiddleware:
    """An encrypted-only entry point to the existing, node-scoped Boot app."""

    def __init__(self, application):
        self.application = application

    def __call__(self, environ, start_response):
        request = webob.Request(environ)
        if (
            request.method != "POST"
            or request.path_qs != boot_identity.RELAY_PATH
            or request.content_type != boot_identity.CONTENT_TYPE
            or request.content_length is None
            or request.content_length > boot_identity.MAX_BODY
        ):
            return webob.Response(status=403)(environ, start_response)
        try:
            host = uuid.UUID(request.headers[boot_identity.HOST_HEADER])
            request_id = str(uuid.UUID(request.headers[boot_identity.NONCE_HEADER]))
            credential = ua_models.NodeEncryptionKey.objects.get_one(
                filters={"uuid": filters.EQ(host)}
            )
            key = base64.b64decode(credential.private_key, validate=True)
            value = boot_identity.decrypt(key, host, request_id, request.body)
            node = uuid.UUID(value["node"])
            method, path = value["method"], value["path"]
            if (
                method not in {"GET", "POST", "PUT", "DELETE", "HEAD"}
                or not isinstance(path, str)
                or not path.startswith("/v1/")
                or path.startswith("//")
                or "#" in path
                or "\\" in path
            ):
                raise ValueError("Invalid delegated request")
            body = base64.b64decode(value["body"], validate=True)
            headers = value["headers"]
            if not isinstance(headers, dict) or any(
                not isinstance(k, str) or not isinstance(v, str)
                for k, v in headers.items()
            ):
                raise ValueError("Invalid delegated headers")
            machine = models.Machine.objects.get_one(filters={"node": filters.EQ(node)})
            pool = models.MachinePool.objects.get_one(
                filters={"uuid": filters.EQ(machine.pool)}
            )
            if pool.hypervisor_node != host:
                raise packers.BootIdentityError(
                    reason="Boot delegate does not own the caller"
                )
            packers.consume_nonce(host, request_id, value["time"])
        except (
            ValueError,
            KeyError,
            TypeError,
            InvalidTag,
            packers.BootIdentityError,
            storage_exc.RecordNotFound,
        ):
            return webob.Response(status=403)(environ, start_response)
        # The verified identity is an internal WSGI value; HTTP cannot supply it.
        headers = {
            k: v
            for k, v in headers.items()
            if k.lower()
            not in {"host", "content-length", "transfer-encoding", "connection"}
        }
        inner = webob.Request.blank(path, method=method, headers=headers, body=body)
        inner.environ[boot_identity.NODE_ENV] = str(node)
        response = inner.get_response(self.application)
        # Leave room for base64 and headers before building the wire envelope.
        if len(response.body) > boot_identity.MAX_RESPONSE * 3 // 4 - 4096:
            response = webob.Response(status=413)
        value = {
            "status": response.status_int,
            "headers": dict(response.headers),
            "body": base64.b64encode(response.body).decode(),
        }
        sealed = boot_identity.encrypt(key, host, request_id, value, response=True)
        if len(sealed) > boot_identity.MAX_RESPONSE:
            sealed = boot_identity.encrypt(
                key,
                host,
                request_id,
                {"status": 413, "headers": {}, "body": ""},
                response=True,
            )
        return webob.Response(body=sealed, content_type=boot_identity.CONTENT_TYPE)(
            environ, start_response
        )
