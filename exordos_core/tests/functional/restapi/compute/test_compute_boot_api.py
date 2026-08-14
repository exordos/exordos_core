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

"""The boot API, served the way the installation serves it: on its own.

A process hosts one restalchemy application. `ResourceMap` is a class
attribute and building an application *replaces* it, and the engine factory
is a singleton the test harness configures and destroys per service -- so a
second application in this process takes both away from the first. The
product never asks it to: `ec-user-api` and `ec-boot-api` are separate units.
This module used to ask it to, and what came of that was decided by the xdist
layout -- a worker that met this class in the middle of its run spent the
rest of it raising `Can not return default engine` at setup, or answering 404
to any request body that named another resource by URI.

So the boot API runs here as it runs there: its own process, reached over
HTTP, with the options it would be started with on the command line. The
database is this worker's, and the schema is the one the `user_api` fixture
has already migrated -- the same schema the tests write their machines into.
"""

import contextlib
import socket
import subprocess
import sys
import tempfile
import time
import typing as tp
from urllib.parse import urljoin
import uuid as sys_uuid

import pytest
import requests
from restalchemy.tests.functional import consts as ra_consts

from exordos_core.common import constants as c
from exordos_core.compute.dm import models


BOOT_API_STARTUP_TIMEOUT = 60.0


def _free_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]


def _read(log: tp.IO[bytes]) -> str:
    """Read back everything the service has written so far."""
    log.seek(0)
    return log.read().decode(errors="replace")


def _wait_until_serving(
    port: int, process: subprocess.Popen, log: tp.IO[bytes]
) -> None:
    """Wait for the port, and give up the moment the process is gone.

    Waiting on a port alone turns a service that died on startup into a
    timeout with nothing to read.
    """
    deadline = time.monotonic() + BOOT_API_STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                "boot API exited with %s before serving:\n%s"
                % (process.returncode, _read(log))
            )
        with contextlib.closing(
            socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ) as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)

    process.kill()
    raise RuntimeError("boot API did not start within %ss" % BOOT_API_STARTUP_TIMEOUT)


@pytest.fixture()
def boot_api(user_api) -> tp.Callable[..., str]:
    """Start a boot API and return the base URL of its v1 API.

    Takes the options the service takes on its command line, so a test says
    what the installation would be configured with rather than reaching into
    a config object it happens to share with the service.

    Depends on `user_api` for the schema: it is the fixture that migrates
    this worker's database before each test, and the boot API reads what the
    test then writes into it.
    """
    processes: tp.List[tp.Tuple[subprocess.Popen, tp.IO[bytes]]] = []

    def start(**options: str) -> str:
        port = _free_port()
        # `ec-boot-api` as a module rather than as the console script: same
        # interpreter, same environment, and no dependence on whether this
        # venv's bin directory happens to be on PATH.
        argv = [
            sys.executable,
            "-m",
            "exordos_core.cmd.boot_api",
            "--boot_api-bind-host",
            "127.0.0.1",
            "--boot_api-bind-port",
            str(port),
            "--db-connection_url",
            ra_consts.get_database_uri(),
        ]
        for name, value in options.items():
            argv += ["--boot_api-%s" % name, str(value)]

        # The service logs for as long as it runs and nothing here reads it
        # while it does, so a pipe would fill its buffer and stop the service
        # mid-test. A file takes all of it, and is still there to be read when
        # the service dies before it ever serves.
        log = tempfile.TemporaryFile()
        process = subprocess.Popen(
            argv,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        processes.append((process, log))
        _wait_until_serving(port, process, log)
        return "http://127.0.0.1:%s/v1/" % port

    yield start

    for process, log in processes:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        log.close()


class TestComputeBootApi:
    def test_netboots_default_net(self, boot_api: tp.Callable[..., str]):
        base_url = boot_api(gc_host="10.20.0.2")

        uuid = sys_uuid.uuid4()
        url = urljoin(base_url, f"boots/{str(uuid)}")

        response = requests.get(url)

        assert response.status_code == 200
        assert response.text.startswith("#!ipxe")
        assert "initrd" in response.text
        assert "vmlinuz" in response.text
        assert "gc_boot_api" in response.text
        assert "tftp://10.20.0.2" in response.text

    def test_netboots_hd_boot(
        self,
        pool_factory: tp.Callable,
        boot_api: tp.Callable[..., str],
    ):
        base_url = boot_api(gc_host="10.20.0.2")

        pool_view = pool_factory()
        pool_view["status"] = "ACTIVE"
        pool = models.MachinePool.restore_from_simple_view(**pool_view)
        pool.insert()

        uuid = sys_uuid.uuid4()
        machine = models.Machine(
            cores=1,
            ram=1024,
            boot="hd0",
            uuid=uuid,
            firmware_uuid=uuid,
            pool=pool.uuid,
            status="ACTIVE",
            project_id=c.ZERO_UUID,
        )
        machine.insert()

        url = urljoin(base_url, f"boots/{machine.uuid}")

        response = requests.get(url)

        assert response.status_code == 200
        assert response.text.startswith("#!ipxe")
        assert "initrd" not in response.text
        assert "vmlinuz" not in response.text
        assert "0x80" in response.text

        machine.delete()
        pool.delete()

    def test_netboots_default_net_custom_kernel_initrd(
        self, boot_api: tp.Callable[..., str]
    ):
        base_url = boot_api(
            gc_host="10.20.0.2",
            kernel="https://kernel.org/vmlinuz",
            initrd="https://kernel.org/initrd.img",
        )

        uuid = sys_uuid.uuid4()
        url = urljoin(base_url, f"boots/{str(uuid)}")

        response = requests.get(url)

        assert response.status_code == 200
        assert response.text.startswith("#!ipxe")
        assert "initrd" in response.text
        assert "vmlinuz" in response.text
        assert "gc_boot_api" in response.text
        assert "tftp://" not in response.text
        assert "https://kernel.org/vmlinuz" in response.text
        assert "https://kernel.org/initrd.img" in response.text
