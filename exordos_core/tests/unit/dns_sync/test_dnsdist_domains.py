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

import pytest

from exordos_core.dns_sync import service as dns_sync_service


@pytest.fixture
def domains_file(tmp_path, monkeypatch):
    path = tmp_path / "etc" / "dnsdist" / "publicdns-domains.txt"
    monkeypatch.setattr(dns_sync_service, "DNSDIST_DOMAINS_FILE", str(path))
    return path


@pytest.fixture
def svc():
    # Skip __init__: it creates an HTTP client and a thread pool
    service = object.__new__(dns_sync_service.DNSSyncService)
    service._dnsdist_domains_content = None
    return service


def _mock_domains(monkeypatch, names):
    session = mock.MagicMock()
    session.execute.return_value.fetchall.return_value = [{"name": n} for n in names]
    engine = mock.MagicMock()
    engine.session_manager.return_value.__enter__.return_value = session
    monkeypatch.setattr(
        dns_sync_service.dns_models.Domain,
        "_get_engine",
        mock.Mock(return_value=engine),
    )
    return session


def test_nested_domains_are_skipped(svc, domains_file, monkeypatch):
    _mock_domains(
        monkeypatch,
        ["exordos.io", "7a61bf.exordos.io", "a.b.exordos.io", "metronom.su"],
    )

    svc._write_dnsdist_domains()

    assert domains_file.read_text() == "exordos.io\nmetronom.su\n"


def test_names_are_normalized(svc, domains_file, monkeypatch):
    _mock_domains(monkeypatch, [" Exordos.IO. ", "exordos.io", "metronom.su."])

    svc._write_dnsdist_domains()

    assert domains_file.read_text() == "exordos.io\nmetronom.su\n"


def test_public_tag_is_required_in_query(svc, domains_file, monkeypatch):
    session = _mock_domains(monkeypatch, ["exordos.io"])

    svc._write_dnsdist_domains()

    query, params = session.execute.call_args.args
    assert "tags @> %s::text[]" in query
    assert "project_id" not in query
    assert params == (["public"],)


def test_unchanged_content_is_not_rewritten(svc, domains_file, monkeypatch):
    _mock_domains(monkeypatch, ["exordos.io"])
    svc._write_dnsdist_domains()
    mtime_ns = domains_file.stat().st_mtime_ns

    with mock.patch.object(dns_sync_service.os, "replace") as replace:
        svc._write_dnsdist_domains()

    replace.assert_not_called()
    assert domains_file.stat().st_mtime_ns == mtime_ns


def test_changed_content_is_rewritten(svc, domains_file, monkeypatch):
    _mock_domains(monkeypatch, ["exordos.io"])
    svc._write_dnsdist_domains()

    _mock_domains(monkeypatch, ["exordos.io", "metronom.su"])
    svc._write_dnsdist_domains()

    assert domains_file.read_text() == "exordos.io\nmetronom.su\n"


def test_no_temp_files_left(svc, domains_file, monkeypatch):
    _mock_domains(monkeypatch, ["exordos.io"])

    svc._write_dnsdist_domains()

    assert [p.name for p in domains_file.parent.iterdir()] == [domains_file.name]
