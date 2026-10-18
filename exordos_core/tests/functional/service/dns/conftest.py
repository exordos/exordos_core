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

from contextlib import closing
import logging
import os
import socket
import subprocess
from urllib.parse import urlparse

import pytest
from restalchemy.tests.functional import consts as ra_c

LOG = logging.getLogger(__name__)

PDNS_BIN = "/usr/sbin/pdns_server"


def find_free_port():
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]


@pytest.fixture()
def pdns_server(user_api, tmp_path_factory: pytest.TempPathFactory):
    result = urlparse(ra_c.get_database_uri())
    if result.scheme != "postgresql":
        pytest.skip("Only PostgreSQL is supported for PowerDNS tests")
    if not os.path.exists(PDNS_BIN):
        pytest.skip("PowerDNS server binary not found, dataplane can't be checked")

    directory = tmp_path_factory.mktemp("pdns")
    config_file = directory / "pdns.conf"
    port = find_free_port()

    config = f"""\
local-port={port}
launch=gpgsql
gpgsql-dbname={result.path[1:]}
gpgsql-user={result.username}
gpgsql-password={result.password or ""}
gpgsql-host={result.hostname}
gpgsql-port={result.port or 5432}
loglevel=100
query-logging=yes
log-dns-queries=yes
"""
    with open(config_file, "w") as f:
        f.write(config)

    proc = subprocess.Popen(
        [
            PDNS_BIN,
            "--guardian=no",
            "--daemon=no",
            "--disable-syslog",
            "--log-timestamp=no",
            "--write-pid=no",
            "--socket-dir=" + str(directory),
            "--config-dir=" + str(directory),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    # Check it started successfully
    assert not proc.poll(), proc.stdout.read().decode("utf-8")

    yield port

    proc.terminate()

    # Useful to debug if things go wrong, will be shown only on test failure.
    LOG.warning("PDNS log:")
    LOG.warning(proc.stdout.read().decode("utf-8"))
