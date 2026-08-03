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

import os
import pwd
import stat
from unittest import mock

import pytest

from exordos_core.cmd import boot_api


def test_nonce_store_uses_worker_identity_before_privilege_drop(tmp_path, monkeypatch):
    directory = tmp_path / "delegations"
    directory.mkdir(mode=0o755)
    monkeypatch.setattr(boot_api.packers, "BOOT_NONCES_DIR", str(directory))
    identity = pwd.getpwuid(os.geteuid())
    with mock.patch.object(boot_api.os, "fchown") as owner:
        boot_api._prepare_nonce_store(identity.pw_name)
    assert owner.call_args.args[1:] == (identity.pw_uid, identity.pw_gid)
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700


def test_nonce_store_rejects_a_symlink(tmp_path, monkeypatch):
    destination = tmp_path / "destination"
    destination.mkdir(mode=0o755)
    directory = tmp_path / "delegations"
    directory.symlink_to(destination, target_is_directory=True)
    monkeypatch.setattr(boot_api.packers, "BOOT_NONCES_DIR", str(directory))
    with mock.patch.object(boot_api.os, "fchown") as owner:
        with pytest.raises(OSError):
            boot_api._prepare_nonce_store(None)
    owner.assert_not_called()
    assert stat.S_IMODE(destination.stat().st_mode) == 0o755


def test_nonce_store_prepared_before_workers_start():
    worker = mock.Mock(__mp_downgrade_user__="worker-user")
    order = []
    worker.start.side_effect = lambda: order.append("start")
    with (
        mock.patch.object(boot_api.config, "parse"),
        mock.patch.object(boot_api.infra_log, "configure"),
        mock.patch.object(boot_api.app, "build_wsgi_application"),
        mock.patch.object(
            boot_api.bjoern_service, "BjoernService", return_value=worker
        ),
        mock.patch.object(
            boot_api,
            "_prepare_nonce_store",
            side_effect=lambda user: order.append(user),
        ),
        mock.patch.object(boot_api.hub, "ProcessHubService"),
    ):
        boot_api.main()
    assert order == ["worker-user", "start"]


def test_nonroot_start_uses_current_process_identity(tmp_path, monkeypatch):
    directory = tmp_path / "delegations"
    monkeypatch.setattr(boot_api.packers, "BOOT_NONCES_DIR", str(directory))
    with (
        mock.patch.object(boot_api.os, "getuid", return_value=1000),
        mock.patch.object(boot_api.os, "geteuid", return_value=1000),
        mock.patch.object(boot_api.os, "getegid", return_value=1001),
        mock.patch.object(boot_api.pwd, "getpwnam") as user,
        mock.patch.object(boot_api.os, "fchown") as owner,
    ):
        boot_api._prepare_nonce_store("nobody")
    user.assert_not_called()
    assert owner.call_args.args[1:] == (1000, 1001)


def test_root_start_provisions_configured_worker_user(tmp_path, monkeypatch):
    directory = tmp_path / "delegations"
    monkeypatch.setattr(boot_api.packers, "BOOT_NONCES_DIR", str(directory))
    with (
        mock.patch.object(boot_api.os, "getuid", return_value=0),
        mock.patch.object(
            boot_api.pwd, "getpwnam", return_value=mock.Mock(pw_uid=65534, pw_gid=65535)
        ) as user,
        mock.patch.object(boot_api.os, "fchown") as owner,
    ):
        boot_api._prepare_nonce_store("boot-worker")
    user.assert_called_once_with("boot-worker")
    assert owner.call_args.args[1:] == (65534, 65535)
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
