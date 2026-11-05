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

"""Core publishes realm snapshots; Ecosystem reconciles upstream ownership."""

from types import SimpleNamespace
from unittest import mock

import pytest

from exordos_core.dns_sync import service


@pytest.fixture
def dns_sync():
    instance = service.DNSSyncService()
    instance._initialized = True
    instance._realm_domains = mock.Mock()
    instance._domain_records = mock.Mock(return_value=[])
    instance._client = mock.Mock()
    yield instance
    instance._executor.shutdown(wait=False)


def test_empty_snapshot_is_published_to_remove_deleted_realm_records(dns_sync):
    domain = SimpleNamespace(name="exordos.io", realm_id="realm1")
    dns_sync._realm_domains.return_value = [domain]

    dns_sync._sync_all_domains("https://ecosystem.test", "realm-uuid", "secret")

    request = dns_sync._client.post.call_args
    assert request.args == (
        "https://ecosystem.test/api/ecosystem/v1/realms/realm-uuid"
        "/actions/sync_dns/invoke",
    )
    assert request.kwargs["json"] == {
        "domain": {"name": "exordos.io", "realm_id": "realm1"},
        "records": [],
    }
    assert request.kwargs["auth"].username == "realm-uuid"
    assert request.kwargs["auth"].password == "secret"
    dns_sync._client.delete.assert_not_called()


def test_one_failed_domain_does_not_prevent_other_snapshots(dns_sync):
    dns_sync._realm_domains.return_value = [
        SimpleNamespace(name="first.test", realm_id="realm1"),
        SimpleNamespace(name="second.test", realm_id="realm1"),
    ]
    dns_sync._client.post.side_effect = [RuntimeError("unavailable"), mock.Mock()]

    dns_sync._sync_all_domains("https://ecosystem.test", "realm-uuid", "secret")

    assert dns_sync._client.post.call_count == 2
    assert dns_sync._client.post.call_args.kwargs["json"]["domain"]["name"] == (
        "second.test"
    )
