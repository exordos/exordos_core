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
import datetime
import time
from unittest import mock
import uuid

import netaddr
import pytest
import webob

from exordos_core.boot_api.api import packers
from exordos_core.cmd import boot_api


@pytest.fixture
def legacy(tmp_path):
    node = uuid.uuid4()
    request = webob.Request.blank("/", environ={"REMOTE_ADDR": "10.60.0.10"})
    leases = tmp_path / "leases"
    expires = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        minutes=10
    )
    leases.write_text(
        "lease 10.60.0.10 {\n  ends 1 %s;\n  binding state active;\n  hardware ethernet 52:54:00:01:02:03;\n}\n"
        % expires.strftime("%Y/%m/%d %H:%M:%S")
    )
    boot_api.CONF.set_override("dhcp_leases_file", str(leases), "boot_api")
    with (
        mock.patch.object(packers.compute_models.Subnet, "objects") as subnets,
        mock.patch.object(packers.compute_models.Port, "objects") as ports,
    ):
        subnets.get_all.return_value = [
            mock.Mock(
                dhcp=False,
                next_server="10.60.0.2",
                ip_discovery_range=True,
                cidr=netaddr.IPNetwork("10.60.0.0/24"),
            )
        ]
        ports.get_one_or_none.return_value = None
        ports.get_one.return_value = mock.Mock(node=node, machine=None)
        yield request, node, ports, leases
    packers.CONF.clear_override("dhcp_leases_file", "boot_api")


@pytest.mark.parametrize("node_field", ["node", "machine"])
def test_legacy_dynamic_boot_lease_maps_mac_not_permanent_ip(legacy, node_field):
    request, node, ports, _ = legacy
    ports.get_one.return_value = mock.Mock(node=None, machine=None)
    setattr(ports.get_one.return_value, node_field, node)
    assert packers.BootEncryptionInformation(request).node_uuid == node
    assert ports.get_one.call_args.kwargs["filters"]["mac"].value == "52:54:00:01:02:03"


def test_legacy_static_boot_port(legacy):
    request, node, ports, _ = legacy
    ports.get_one_or_none.return_value = mock.Mock(node=node, machine=None)
    assert packers.BootEncryptionInformation(request).node_uuid == node
    ports.get_one.assert_not_called()


@pytest.mark.parametrize(
    "failure",
    ["underlay", "wrong_header", "missing", "expired", "freed", "stale_then_freed"],
)
def test_legacy_identity_fails_closed(legacy, failure):
    request, _, ports, leases = legacy
    if failure == "underlay":
        request.remote_addr = "192.0.2.10"
    elif failure == "wrong_header":
        request.headers["X-Genesis-Node-UUID"] = str(uuid.uuid4())
    elif failure == "missing":
        leases.unlink()
    elif failure == "expired":
        leases.write_text(
            leases.read_text().replace("ends 1 ", "ends 1 2000/01/01 00:00:00; # ")
        )
    elif failure == "freed":
        leases.write_text(
            leases.read_text().replace("binding state active;", "binding state free;")
        )
    else:
        leases.write_text(
            leases.read_text() + "lease 10.60.0.10 {\n binding state free;\n}\n"
        )
    with pytest.raises(packers.BootIdentityError):
        packers.BootEncryptionInformation(request).node_uuid


def test_delegated_node_is_internal_wsgi_identity():
    node = uuid.uuid4()
    request = webob.Request.blank(
        "/", environ={packers.boot_identity.NODE_ENV: str(node)}
    )
    with mock.patch.object(packers.compute_models.Subnet, "objects") as subnets:
        assert packers.BootEncryptionInformation(request).node_uuid == node
    subnets.get_all.assert_not_called()


def test_replay_store_is_atomic_and_rejects_expiry(tmp_path, monkeypatch):
    monkeypatch.setattr(packers, "BOOT_NONCES_DIR", str(tmp_path))
    host, nonce = uuid.uuid4(), str(uuid.uuid4())
    now = int(time.time())
    packers.consume_nonce(host, nonce, now)
    with pytest.raises(packers.BootIdentityError, match="Replayed"):
        packers.consume_nonce(host, nonce, now)
    for timestamp in [now - 61, now + 60, "0", True]:
        with pytest.raises(packers.BootIdentityError, match="Expired"):
            packers.consume_nonce(host, str(uuid.uuid4()), timestamp)


@pytest.mark.parametrize("kind", ["guest_machine", "config"])
@pytest.mark.parametrize("operation", ["get", "update", "delete"])
def test_provisioning_and_runtime_facts_require_the_caller(kind, operation):
    from gcl_sdk.agents.universal.status_api.dm import models
    from exordos_core.boot_api.api import controllers

    node = uuid.uuid4()
    request = mock.Mock()
    request.context.encryption_information.node_uuid = node
    controller = controllers.BootResourcesController(request)
    with mock.patch.object(models.Resource, "objects") as objects:
        objects.get_one.return_value = mock.Mock()
        getattr(controller, operation)(
            uuid=uuid.uuid4(), parent_resource=models.KindModel(kind=kind)
        )
    assert objects.get_one.call_args.kwargs["filters"]["node"].value == node


def test_key_refresh_requires_own_server_authorized_installation():
    from exordos_core.boot_api.api import controllers

    node = uuid.uuid4()
    request = mock.Mock()
    request.context.encryption_information.node_uuid = node
    controller = controllers.BootNodesController(request)
    resource = mock.Mock(uuid=node)
    invoke = controllers.BootNodesController.refresh_secret._post
    with mock.patch.object(controllers.compute_models.Machine, "objects") as machines:
        machines.get_one.return_value = mock.Mock(boot="hd0", image="image")
        with pytest.raises(packers.BootIdentityError):
            invoke(controller, resource)
        with pytest.raises(packers.BootIdentityError):
            invoke(controller, mock.Mock(uuid=uuid.uuid4()))
        resource.save.assert_not_called()
        machines.get_one.return_value.boot = "network"
        result = invoke(controller, resource)
        assert result["key"] == resource.private_key
        assert len(result["key"]) == 44
        resource.save.assert_called_once_with()


@pytest.mark.parametrize("delegated", [False, True])
def test_netboot_uses_the_transport_endpoint_for_its_caller(delegated):
    from exordos_core.boot_api.api import controllers
    from restalchemy.api import controllers as ra_controllers

    node = uuid.uuid4()
    request = mock.Mock()
    request.environ = {packers.boot_identity.NODE_ENV: str(node)} if delegated else {}
    request.context.encryption_information.node_uuid = node
    controller = controllers.NetBootController(request)
    machine = mock.Mock()
    packers.CONF.set_override("gc_boot_api", "http://boot:11013", "boot_api")
    packers.CONF.set_override("proxy_endpoint", "http://core:11015", "boot_api")
    try:
        with mock.patch.object(
            ra_controllers.BaseResourceController, "get", return_value=machine
        ):
            controller.get(node)
        endpoint = "http://core:11015" if delegated else "http://boot:11013"
        assert machine.set_netboot_params.call_args.args[1] == endpoint
    finally:
        packers.CONF.clear_override("gc_boot_api", "boot_api")
        packers.CONF.clear_override("proxy_endpoint", "boot_api")
