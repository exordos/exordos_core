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

"""The fabric's systemd units, as the image installs them."""

import pathlib
import shlex
import configparser

UNITS = pathlib.Path(__file__).parents[4] / "etc/evpn"


def _exec_start(unit):
    for line in (UNITS / unit).read_text(encoding="utf-8").splitlines():
        if line.startswith("ExecStart="):
            return shlex.split(line.partition("=")[2])
    raise AssertionError(f"{unit} has no ExecStart")


def test_gobgpd_api_is_root_only():
    argv = _exec_start("gobgpd.service")
    assert argv[argv.index("--api-hosts") + 1] == "unix:///run/gobgpd/api.sock"
    assert "--pprof-disable" in argv
    assert "--metrics-path=" in argv
    unit = configparser.ConfigParser(interpolation=None)
    unit.read(UNITS / "gobgpd.service")
    service = unit["Service"]
    assert service["User"] == "root"
    assert service["RuntimeDirectory"] == "gobgpd"
    assert service["RuntimeDirectoryMode"] == "0700"
    assert service["UMask"] == "0077"
    assert shlex.split(service["ExecStartPre"]) == [
        "/usr/bin/rm",
        "-f",
        "/run/gobgpd/api.sock",
    ]
