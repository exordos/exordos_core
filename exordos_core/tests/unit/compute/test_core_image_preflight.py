#    Copyright 2026 Genesis Corporation.
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

import http.server
import threading
from types import SimpleNamespace
from unittest import mock
import uuid

import pytest
from restalchemy.storage.sql import orm

from exordos_core.compute.builders import pool as builder
from exordos_core.repo.dm import models as repo_models


@pytest.fixture
def image_server():
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            status = {"/missing": 404, "/unavailable": 503}.get(self.path, 200)
            body = b"image" if self.path.endswith(".raw.zst") else b""
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def core_machine(monkeypatch):
    service = builder.PoolBuilderService(uuid.uuid4(), mock.Mock())
    machine = mock.Mock(
        uuid=uuid.uuid4(),
        boot="hd0",
        image="old-image",
        node=SimpleNamespace(uuid=uuid.uuid4(), hostname="core", name="core"),
    )
    machine.name = "core-set-node-0"
    agent_uuid = uuid.uuid4()
    port = mock.Mock()
    volume = SimpleNamespace(image="old-image")
    monkeypatch.setattr(service, "_get_or_fetch_machine_ctx", lambda _: (port, volume))
    monkeypatch.setattr(service, "_agent_by_pool", lambda _: agent_uuid)
    agent_lookup = mock.Mock(return_value=SimpleNamespace(uuid=agent_uuid))
    monkeypatch.setattr(orm.ObjectCollection, "get_one_or_none", agent_lookup)
    pool_machine = mock.Mock()
    monkeypatch.setattr(
        builder.pool_models.PoolMachine, "from_machine_and_port", pool_machine
    )
    monkeypatch.setattr(builder.models.Port, "from_boot_network", lambda: port)
    return service, machine, volume, agent_lookup, pool_machine


@pytest.mark.parametrize("path", ["/missing", "/unavailable", "/empty"])
@pytest.mark.parametrize("has_report", [True, False])
def test_unreachable_core_image_keeps_previous_target(
    core_machine, image_server, path, has_report
):
    service, machine, volume, agents, pool_machine = core_machine
    base_url, requests = image_server
    volume.image = base_url + path
    old = SimpleNamespace(image="old-image")
    with pytest.raises(ValueError, match="Core update image"):
        service._actualize_machine_derivatives_on_create_update(
            machine, machine_guest_pair=(old, old if has_report else None)
        )
    assert requests == [path]
    assert machine.boot == "hd0"
    assert machine.image == "old-image"
    agents.assert_not_called()
    pool_machine.assert_not_called()


def test_reachable_core_image_reaches_guest_target(core_machine, image_server):
    service, machine, volume, _, _ = core_machine
    base_url, requests = image_server
    volume.image = base_url + "/image.raw.zst"
    old = SimpleNamespace(image="old-image")
    _, guest = service._actualize_machine_derivatives_on_create_update(
        machine, machine_guest_pair=(old, old)
    )
    assert guest.image == volume.image
    assert guest.boot == "hd0"
    assert requests == ["/image.raw.zst"]


def test_indexed_image_uses_selected_repository(
    core_machine, image_server, monkeypatch
):
    service, machine, volume, _, _ = core_machine
    base_url, requests = image_server
    image_uuid = str(uuid.uuid4())
    inventory = {
        "name": "core",
        "version": "0.0.0.dev1+cassi.local",
        "index": {"images": {image_uuid: "exordos-core.raw.zst"}},
    }
    artifacts = []
    for name, priority, url in [
        ("published", 0, "https://example.invalid/exordos-elements/"),
        ("selected", 4096, base_url + "/exordos-elements/"),
    ]:
        repository = repo_models.Repository(
            name=name,
            project_id=uuid.uuid4(),
            priority=priority,
            driver_spec=repo_models.NginxDriverSpec(url=url),
        )
        _, indexed = repo_models.RepoElement.from_inventory(repository, inventory)
        artifacts.extend(indexed)
    monkeypatch.setattr(
        orm.ObjectCollection, "get_all", mock.Mock(return_value=artifacts)
    )
    volume.image = artifacts[0].urn
    old = SimpleNamespace(image="old-image")
    _, guest = service._actualize_machine_derivatives_on_create_update(
        machine, machine_guest_pair=(old, old)
    )
    assert guest.image == artifacts[1].uri
    assert requests == [
        "/exordos-elements/core/0.0.0.dev1+cassi.local/images/exordos-core.raw.zst"
    ]


@pytest.mark.parametrize("image", [None, ""])
def test_missing_core_image_keeps_previous_target(core_machine, image):
    service, machine, volume, agents, pool_machine = core_machine
    volume.image = image
    old = SimpleNamespace(image="old-image")
    with pytest.raises(ValueError, match="Core update image"):
        service._actualize_machine_derivatives_on_create_update(
            machine, machine_guest_pair=(old, old)
        )
    assert machine.image == "old-image"
    agents.assert_not_called()
    pool_machine.assert_not_called()


def test_core_image_timeout_keeps_previous_target(core_machine, monkeypatch):
    service, machine, volume, agents, pool_machine = core_machine
    volume.image = "https://example.invalid/image.raw.zst"
    probe = mock.Mock(side_effect=TimeoutError)
    monkeypatch.setattr("urllib.request.urlopen", probe)
    old = SimpleNamespace(image="old-image")
    with pytest.raises(ValueError, match="Core update image"):
        service._actualize_machine_derivatives_on_create_update(
            machine, machine_guest_pair=(old, old)
        )
    assert probe.call_args.kwargs["timeout"] == 5
    assert machine.image == "old-image"
    agents.assert_not_called()
    pool_machine.assert_not_called()


@pytest.mark.parametrize("core", [True, False])
def test_unchanged_image_does_not_require_repository_access(
    core_machine, monkeypatch, core
):
    service, machine, volume, _, _ = core_machine
    if not core:
        machine.name = "application-node-0"
    probe = mock.Mock(side_effect=AssertionError("Unexpected image probe"))
    monkeypatch.setattr("urllib.request.urlopen", probe)
    old = SimpleNamespace(image=volume.image)
    _, guest = service._actualize_machine_derivatives_on_create_update(
        machine, machine_guest_pair=(old, old)
    )
    assert guest.image == volume.image
    probe.assert_not_called()
