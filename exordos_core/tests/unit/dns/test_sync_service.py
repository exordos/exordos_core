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

from types import SimpleNamespace
from unittest import mock

import pytest

from exordos_core.common import constants as c
from exordos_core.dns_sync import service as dns_sync


@pytest.fixture
def service():
    instance = dns_sync.DNSSyncService()
    yield instance
    instance._executor.shutdown(wait=False)


def test_sync_uses_realm_authenticated_ecosystem_action(service):
    service._client = mock.Mock()
    domain = SimpleNamespace(name="exordos.io", realm_id="a5f3957")
    auth = mock.sentinel.auth
    records = [{"uuid": "record", "full_name": "workspace-a5f3957.exordos.io"}]

    service._eco_sync_domain(
        "https://ecosystem.example.com",
        "realm-uuid",
        auth,
        domain,
        records,
    )

    service._client.post.assert_called_once_with(
        "https://ecosystem.example.com/api/ecosystem/v1/realms/realm-uuid"
        "/actions/sync_dns/invoke",
        auth=auth,
        json={
            "domain": {"name": "exordos.io", "realm_id": "a5f3957"},
            "records": records,
        },
    )


def test_dns_sync_does_not_require_shared_project_access_token(service):
    values = {
        c.VAR_ECOSYSTEM_ENDPOINT_UUID: "https://ecosystem.example.com",
        c.VAR_REALM_UUID_UUID: "realm-uuid",
        c.VAR_REALM_SECRET_UUID: "realm-secret",
    }
    service._get_variable_value = values.get

    assert service._get_ecosystem_credentials() == (
        "https://ecosystem.example.com",
        "realm-uuid",
        "realm-secret",
    )


def test_initialization_migrates_the_legacy_realm_domain(service):
    legacy_domain = mock.Mock(
        realm_id=None,
        sync_only=False,
        sync_to_ecosystem=True,
    )
    soa = mock.Mock(type="SOA")
    record = mock.Mock(type="A")
    service._realm_domains = mock.Mock(return_value=[])
    service._eco_get_realm = mock.Mock(
        return_value={
            "realm_id": "a5f3957",
            "domain": "a5f3957.exordos.io",
        }
    )

    with (
        mock.patch.object(dns_sync.dns_models, "Domain") as domain_model,
        mock.patch.object(dns_sync.dns_models, "Record") as record_model,
    ):
        domain_model.objects.get_one_or_none.side_effect = [None, legacy_domain]
        record_model.objects.get_all.return_value = [soa, record]

        assert service._ensure_realm_domain(
            "https://ecosystem.example.com",
            "realm-uuid",
            mock.sentinel.auth,
        )

    assert legacy_domain.name == "exordos.io"
    assert legacy_domain.realm_id == "a5f3957"
    assert legacy_domain.sync_only is True
    legacy_domain.update.assert_called_once_with()
    soa.delete.assert_called_once_with(force=True)
    record.update.assert_called_once_with()
