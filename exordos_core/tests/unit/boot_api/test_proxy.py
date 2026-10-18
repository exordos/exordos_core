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
import time
from unittest import mock
import uuid

import pytest
import webob

from gcl_sdk.agents.universal.api import boot_identity
from exordos_core.boot_api.api import proxy


@pytest.fixture
def relay(tmp_path, monkeypatch):
    key = b"k" * 32
    host, node = uuid.uuid4(), uuid.uuid4()
    invoked = []

    def application(environ, start_response):
        invoked.append(environ)
        assert environ[boot_identity.NODE_ENV] == str(node)
        return webob.Response(json={"secret": "response-sentinel"})(
            environ, start_response
        )

    monkeypatch.setattr(proxy.packers, "BOOT_NONCES_DIR", str(tmp_path))
    with (
        mock.patch.object(proxy.ua_models.NodeEncryptionKey, "objects") as keys,
        mock.patch.object(proxy.models.Machine, "objects") as machines,
        mock.patch.object(proxy.models.MachinePool, "objects") as pools,
    ):
        keys.get_one.return_value = mock.Mock(
            private_key=base64.b64encode(key).decode()
        )
        machines.get_one.return_value = mock.Mock(pool=uuid.uuid4())
        pools.get_one.return_value = mock.Mock(hypervisor_node=host)
        yield proxy.BootProxyMiddleware(application), key, host, node, pools, invoked


def request_for(key, host, node, **changes):
    request_id = str(uuid.uuid4())
    value = {
        "node": str(node),
        "method": "GET",
        "path": "/v1/kind/guest_machine/resources/" + str(node),
        "headers": {},
        "body": "",
        "time": int(time.time()),
    }
    value.update(changes)
    request = webob.Request.blank(
        boot_identity.RELAY_PATH,
        method="POST",
        body=boot_identity.encrypt(key, host, request_id, value),
        content_type=boot_identity.CONTENT_TYPE,
        headers={
            boot_identity.HOST_HEADER: str(host),
            boot_identity.NONCE_HEADER: request_id,
        },
    )
    return request, request_id


def test_both_directions_are_encrypted_and_replay_is_rejected(relay):
    app, key, host, node, _, invoked = relay
    request, request_id = request_for(key, host, node)
    response = request.get_response(app)
    assert response.status_int == 200
    assert response.content_type == boot_identity.CONTENT_TYPE
    assert b"response-sentinel" not in response.body
    value = boot_identity.decrypt(key, host, request_id, response.body, response=True)
    assert b"response-sentinel" in base64.b64decode(value["body"])
    assert value["status"] == 200
    assert request.get_response(app).status_int == 403
    assert len(invoked) == 1


@pytest.mark.parametrize(
    "failure",
    [
        "plain",
        "wrong_key",
        "wrong_host",
        "wrong_request",
        "tampered",
        "expired",
        "future",
        "foreign_pool",
        "absolute_path",
        "bad_method",
        "bad_body",
    ],
)
def test_invalid_transport_never_reaches_a_handler(relay, failure):
    app, key, host, node, pools, invoked = relay
    changes = {}
    if failure == "expired":
        changes["time"] = int(time.time()) - 61
    if failure == "future":
        changes["time"] = int(time.time()) + 60
    if failure == "absolute_path":
        changes["path"] = "http://attacker/v1/nodes/"
    if failure == "bad_method":
        changes["method"] = "CONNECT"
    if failure == "bad_body":
        changes["body"] = "???"
    request, _ = request_for(
        b"x" * 32 if failure == "wrong_key" else key, host, node, **changes
    )
    if failure == "plain":
        request.body = b'{"node":"sentinel"}'
    if failure == "wrong_host":
        request.headers[boot_identity.HOST_HEADER] = str(uuid.uuid4())
    if failure == "wrong_request":
        request.headers[boot_identity.NONCE_HEADER] = str(uuid.uuid4())
    if failure == "tampered":
        request.body = request.body[:-1] + bytes([request.body[-1] ^ 1])
    if failure == "foreign_pool":
        pools.get_one.return_value.hypervisor_node = uuid.uuid4()
    assert request.get_response(app).status_int == 403
    assert not invoked


def test_proxy_listener_rejects_ordinary_boot_routes(relay):
    app, _, _, node, _, invoked = relay
    request = webob.Request.blank("/v1/nodes/" + str(node))
    assert request.get_response(app).status_int == 403
    assert not invoked


@pytest.mark.parametrize("size, expected", [(3 << 20, 200), (25 << 20, 413)])
def test_large_payloads_have_a_separate_response_limit(relay, size, expected):
    app, key, host, node, _, _ = relay
    body = b"x" * size

    def application(environ, start_response):
        return webob.Response(body=body)(environ, start_response)

    app.application = application
    request, request_id = request_for(key, host, node)
    response = request.get_response(app)
    assert response.status_int == 200
    assert len(response.body) <= boot_identity.MAX_RESPONSE
    value = boot_identity.decrypt(key, host, request_id, response.body, response=True)
    assert value["status"] == expected
    if expected == 200:
        assert base64.b64decode(value["body"]) == body
