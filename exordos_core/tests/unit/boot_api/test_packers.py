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
import base64
import time
from unittest import mock
import uuid

import pytest
import webob

from exordos_core.boot_api.api import packers


def test_boot_direct_identity_requires_source_port():
    node = uuid.uuid4()
    request = webob.Request.blank("/", headers={"X-Genesis-Node-UUID": str(node)})
    with mock.patch.object(packers.compute_models.Port, "objects") as objects:
        objects.get_one.return_value = mock.Mock(node=node, machine=None)
        request.remote_addr = "10.61.0.10"
        assert packers.BootEncryptionInformation(request).node_uuid == node
        objects.get_one.assert_called_once()


def test_boot_rejects_invalid_identity():
    request = webob.Request.blank("/", headers={"X-Genesis-Node-UUID": "invalid"})
    with pytest.raises(packers.ra_exceptions.ParseError):
        packers.BootEncryptionInformation(request).node_uuid


@pytest.mark.parametrize("node_field", ["node", "machine"])
def test_boot_legacy_port_identity(node_field):
    node = uuid.uuid4()
    port = mock.Mock(node=None, machine=None)
    setattr(port, node_field, node)
    request = webob.Request.blank("/", environ={"REMOTE_ADDR": "10.60.0.10"})
    with mock.patch.object(packers.compute_models.Port, "objects") as objects:
        objects.get_one.return_value = port
        assert packers.BootEncryptionInformation(request).node_uuid == node
        assert (
            str(objects.get_one.call_args.kwargs["filters"]["ipv4"].value)
            == request.remote_addr
        )


def test_boot_rejects_unassigned_port():
    request = webob.Request.blank("/", environ={"REMOTE_ADDR": "10.60.0.10"})
    with mock.patch.object(packers.compute_models.Port, "objects") as objects:
        objects.get_one.return_value = mock.Mock(node=None, machine=None)
        with pytest.raises(packers.BootIdentityError, match="identity"):
            packers.BootEncryptionInformation(request).node_uuid


def test_boot_direct_foreign_header_is_rejected():
    request = webob.Request.blank(
        "/",
        headers={"X-Genesis-Node-UUID": str(uuid.uuid4())},
        environ={"REMOTE_ADDR": "10.61.0.10"},
    )
    with mock.patch.object(packers.compute_models.Port, "objects") as objects:
        objects.get_one.return_value = mock.Mock(node=uuid.uuid4(), machine=None)
        with pytest.raises(packers.BootIdentityError, match="identity"):
            packers.BootEncryptionInformation(request).node_uuid


@pytest.fixture
def delegation(tmp_path, monkeypatch):
    host, node = uuid.uuid4(), uuid.uuid4()
    request = webob.Request.blank(
        "/v1/kind/config/resources/?hash=a", method="POST", body=b'{"value":{}}'
    )
    headers = request.headers
    headers["X-Genesis-Node-UUID"] = str(node)
    headers[packers.boot_identity.HOST_HEADER] = str(host)
    headers[packers.boot_identity.TIME_HEADER] = str(int(time.time()))
    headers[packers.boot_identity.NONCE_HEADER] = str(uuid.uuid4())
    headers[packers.boot_identity.SIGNATURE_HEADER] = packers.boot_identity.signature(
        b"k" * 32,
        host,
        node,
        headers[packers.boot_identity.TIME_HEADER],
        headers[packers.boot_identity.NONCE_HEADER],
        request.method,
        request.path_qs,
        request.body,
    )
    monkeypatch.setattr(packers, "BOOT_NONCES_DIR", str(tmp_path / "nonces"))
    with (
        mock.patch.object(packers.ua_models.NodeEncryptionKey, "objects") as keys,
        mock.patch.object(packers.compute_models.Machine, "objects") as machines,
        mock.patch.object(packers.compute_models.MachinePool, "objects") as pools,
    ):
        keys.get_one.return_value = mock.Mock(
            private_key=base64.b64encode(b"k" * 32).decode()
        )
        machines.get_one.return_value = mock.Mock(pool=uuid.uuid4())
        pools.get_one.return_value = mock.Mock(hypervisor_node=host)
        yield request, node, pools


def test_boot_delegated_identity_and_replay(delegation):
    request, node, _ = delegation
    information = packers.BootEncryptionInformation(request)
    assert information.node_uuid == node
    assert information.node_uuid == node
    with pytest.raises(packers.BootIdentityError, match="Replayed"):
        packers.BootEncryptionInformation(request).node_uuid


@pytest.mark.parametrize(
    "tamper",
    [
        "body",
        "path",
        "method",
        "node",
        "nonce",
        "signature",
        "expired",
        "future",
        "foreign_host",
    ],
)
def test_boot_delegation_refuses_tampering(delegation, tamper):
    request, _, pools = delegation
    if tamper == "body":
        request.body = b'{"value":{"bad":true}}'
    elif tamper == "path":
        request.path_info = "/v1/kind/config/resources/victim"
    elif tamper == "method":
        request.method = "DELETE"
    elif tamper == "node":
        request.headers["X-Genesis-Node-UUID"] = str(uuid.uuid4())
    elif tamper == "nonce":
        request.headers[packers.boot_identity.NONCE_HEADER] = str(uuid.uuid4())
    elif tamper == "signature":
        request.headers[packers.boot_identity.SIGNATURE_HEADER] = "a" * 64
    elif tamper in ("expired", "future"):
        request.headers[packers.boot_identity.TIME_HEADER] = str(
            int(time.time()) + (-61 if tamper == "expired" else 1)
        )
    else:
        pools.get_one.return_value.hypervisor_node = uuid.uuid4()
    with pytest.raises(packers.BootIdentityError):
        packers.BootEncryptionInformation(request).node_uuid


def test_legacy_guest_machine_does_not_resolve_scoped_identity():
    from gcl_sdk.agents.universal.status_api.dm import models

    from exordos_core.boot_api.api import controllers

    request = mock.Mock()

    class Information:
        @property
        def node_uuid(self):
            raise AssertionError("legacy provisioning must not resolve scoped identity")

    request.context.encryption_information = Information()
    controller = controllers.BootResourcesController(request)
    kind = models.KindModel(kind="guest_machine")
    with mock.patch.object(models.Resource, "insert"):
        resource = controller.create(
            parent_resource=kind, uuid=uuid.uuid4(), node=uuid.uuid4(), value={}
        )
    assert resource.kind.kind == "guest_machine"


def test_legacy_collection_filter_cannot_select_runtime_facts():
    from exordos_core.boot_api.api import controllers
    from gcl_sdk.agents.universal.status_api.dm import models

    controller = controllers.BootResourcesController(mock.Mock())
    kind = models.KindModel(kind="guest_machine")
    with mock.patch.object(models.Resource, "objects") as objects:
        controller.filter(kind, {"kind": packers.dm_filters.EQ("config")})
    assert objects.get_all.call_args.kwargs["filters"]["kind"].value == "guest_machine"


@pytest.mark.parametrize("operation", ["get", "update", "delete"])
def test_legacy_guest_machine_read_write_keeps_dynamic_lease_contract(operation):
    from exordos_core.boot_api.api import controllers
    from gcl_sdk.agents.universal.status_api.dm import models

    class Information:
        @property
        def node_uuid(self):
            raise AssertionError("legacy provisioning has no source Port")

    request = mock.Mock()
    request.context.encryption_information = Information()
    controller = controllers.BootResourcesController(request)
    kind = models.KindModel(kind="guest_machine")
    resource = mock.Mock()
    with mock.patch.object(models.Resource, "objects") as objects:
        objects.get_one.return_value = resource
        getattr(controller, operation)(uuid=uuid.uuid4(), parent_resource=kind)
    assert objects.get_one.call_args.kwargs["filters"]["kind"].value == "guest_machine"


@pytest.mark.parametrize("malformed", [False, True])
def test_identity_refusal_has_nonretryable_http_status(delegation, malformed):
    from restalchemy.api.middlewares import errors

    request, _, pools = delegation
    if malformed:
        request.headers[packers.boot_identity.NONCE_HEADER] = "malformed"
    else:
        pools.get_one.return_value.hypervisor_node = uuid.uuid4()

    def application(environ, start_response):
        packers.BootEncryptionInformation(request).node_uuid
        raise AssertionError("invalid caller reached application")

    response = request.get_response(errors.ErrorsHandlerMiddleware(application))
    assert response.status_code == (400 if malformed else 403)
