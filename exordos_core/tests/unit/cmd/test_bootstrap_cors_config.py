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

from unittest import mock

from exordos_core.cmd import bootstrap

_CORE_CONFIG = """\
[DEFAULT]
verbose = True

[user_api]
bind_host = 0.0.0.0
bind_port = 11010

[iam]
global_salt = deadbeef
"""


def _run(tmp_path, spec, content=_CORE_CONFIG):
    etc_path = tmp_path / "exordos_core.conf"
    data_path = tmp_path / "data" / "exordos_core.conf"
    if content is not None:
        etc_path.write_text(content, encoding="utf-8")
    with (
        mock.patch.object(bootstrap, "CORE_CONFIG_PATH", str(etc_path)),
        mock.patch.object(bootstrap, "CORE_CONFIG_DATA_PATH", str(data_path)),
        mock.patch.object(bootstrap.subprocess, "run") as run,
    ):
        bootstrap._ensure_cors_config(spec)
    return etc_path, data_path, run


def test_sets_origins_in_user_api_section(tmp_path):
    spec = {"cors_allowed_origins": ["https://example.com", "https://app.example.com"]}

    etc_path, data_path, run = _run(tmp_path, spec)

    content = etc_path.read_text(encoding="utf-8")
    assert (
        "[user_api]\n"
        "cors_allowed_origins = https://example.com,https://app.example.com\n"
    ) in content
    # The rest of the config survives the rewrite
    assert "bind_port = 11010" in content
    assert "global_salt = deadbeef" in content
    # The persisted copy is kept in sync
    assert data_path.read_text(encoding="utf-8") == content
    run.assert_called_once()
    assert "try-restart" in run.call_args.args[0]


def test_existing_value_is_replaced(tmp_path):
    _run(tmp_path, {"cors_allowed_origins": ["https://old.example.com"]})

    etc_path, _, run = _run(
        tmp_path,
        {"cors_allowed_origins": ["https://new.example.com"]},
        content=None,
    )

    content = etc_path.read_text(encoding="utf-8")
    assert content.count("cors_allowed_origins") == 1
    assert "cors_allowed_origins = https://new.example.com\n" in content
    run.assert_called_once()


def test_unchanged_origins_are_a_noop(tmp_path):
    spec = {"cors_allowed_origins": ["https://example.com"]}
    etc_path, _, _ = _run(tmp_path, spec)
    content = etc_path.read_text(encoding="utf-8")

    etc_path, _, run = _run(tmp_path, spec, content=None)

    assert etc_path.read_text(encoding="utf-8") == content
    run.assert_not_called()


def test_spec_without_origins_is_skipped(tmp_path):
    etc_path, data_path, run = _run(tmp_path, {"cors_allowed_origins": []})

    assert etc_path.read_text(encoding="utf-8") == _CORE_CONFIG
    assert not data_path.exists()
    run.assert_not_called()


def test_missing_user_api_section_is_skipped(tmp_path):
    spec = {"cors_allowed_origins": ["https://example.com"]}

    etc_path, data_path, run = _run(
        tmp_path, spec, content="[DEFAULT]\nverbose = True\n"
    )

    assert "cors_allowed_origins" not in etc_path.read_text(encoding="utf-8")
    assert not data_path.exists()
    run.assert_not_called()


def test_missing_config_is_skipped(tmp_path):
    spec = {"cors_allowed_origins": ["https://example.com"]}

    etc_path, data_path, run = _run(tmp_path, spec, content=None)

    assert not etc_path.exists()
    assert not data_path.exists()
    run.assert_not_called()
