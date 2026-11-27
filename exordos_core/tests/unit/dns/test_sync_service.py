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
            [],
        )

    assert legacy_domain.name == "exordos.io"
    assert legacy_domain.realm_id == "a5f3957"
    assert legacy_domain.sync_only is True
    legacy_domain.update.assert_called_once_with()
    soa.delete.assert_called_once_with(force=True)
    record.update.assert_called_once_with()


def test_initialization_preserves_local_authoritative_zone(service):
    domain = mock.Mock(
        realm_id=None,
        sync_only=False,
        sync_to_ecosystem=False,
    )
    service._eco_get_realm = mock.Mock(
        return_value={"realm_id": "a5f3957", "domain": "a5f3957.exordos.io"}
    )
    service._eco_sync_domain = mock.Mock()

    with mock.patch.object(dns_sync.dns_models, "Domain") as domain_model:
        domain_model.objects.get_all.return_value = []
        domain_model.objects.get_one_or_none.return_value = domain

        service._sync_all_domains(
            "https://ecosystem.example.com", "realm-uuid", "realm-secret"
        )

    assert service._initialized is False
    domain.update.assert_not_called()
    domain_model.assert_not_called()
    service._eco_sync_domain.assert_not_called()


def test_initialization_rejects_conflicting_sync_zone(service):
    service._eco_get_realm = mock.Mock(
        return_value={"realm_id": "a5f3957", "domain": "a5f3957.exordos.io"}
    )
    domain = mock.Mock(realm_id=None, sync_only=False, sync_to_ecosystem=True)

    with mock.patch.object(dns_sync.dns_models, "Domain") as domain_model:
        domain_model.objects.get_all.return_value = []
        domain_model.objects.get_one_or_none.return_value = domain

        with pytest.raises(ValueError, match="Local DNS domain conflicts"):
            service._ensure_realm_domain(
                "https://ecosystem.example.com", "realm-uuid", mock.sentinel.auth, []
            )

    domain.update.assert_not_called()


def test_custom_zone_snapshot_uses_normal_names_and_realm_auth(service):
    custom = SimpleNamespace(name="slavoniy.space", realm_id=None)
    scoped = SimpleNamespace(name="exordos.io", realm_id="a5f3957")
    service._domain_records = mock.Mock(return_value=[{"full_name": "slavoniy.space"}])
    service._client = mock.Mock()
    with mock.patch.object(dns_sync.dns_models.Domain, "objects") as domains:
        domains.get_all.return_value = [scoped, custom]
        service._sync_custom_zones(
            "https://eco", "realm", mock.sentinel.auth, [scoped, custom]
        )
    service._domain_records.assert_called_once_with(custom)
    service._client.post.assert_called_once_with(
        "https://eco/api/ecosystem/v1/realms/realm/actions/sync_dns/invoke",
        auth=mock.sentinel.auth,
        json={
            "zones": [
                {"name": "slavoniy.space", "records": [{"full_name": "slavoniy.space"}]}
            ]
        },
    )
    domains.get_all.assert_not_called()


def test_empty_inventory_releases_remote_state_after_restart(service):
    service._client = mock.Mock()
    service._ensure_realm_domain = mock.Mock(return_value=True)
    with mock.patch.object(dns_sync.dns_models.Domain, "objects") as domains:
        domains.get_all.return_value = []
        service._sync_all_domains("https://eco", "realm", "secret")
    assert service._client.post.call_args.kwargs["json"] == {"zones": []}
    auth = service._client.post.call_args.kwargs["auth"]
    assert (auth.username, auth.password) == ("realm", "secret")


def test_custom_zone_deletion_is_sent_on_full_sync_without_record_updates(service):
    service._client = mock.Mock()
    service._initialized = True
    service._last_sync_dt = mock.sentinel.last
    service._last_full_sync_at = 0
    service._has_recent_changes = mock.Mock(return_value=False)
    with (
        mock.patch.object(
            dns_sync.time, "monotonic", return_value=dns_sync.FULL_SYNC_INTERVAL + 1
        ),
        mock.patch.object(dns_sync.dns_models.Domain, "objects") as domains,
    ):
        domains.get_all.return_value = []
        service._sync_all_domains("https://eco", "realm", "secret")
    assert service._client.post.call_args.kwargs["json"] == {"zones": []}


def test_failed_custom_snapshot_is_retried_without_advancing_cursor(service):
    service._client = mock.Mock()
    service._client.post.side_effect = RuntimeError("unavailable")
    service._initialized = True
    previous = service._last_full_sync_at
    with mock.patch.object(dns_sync.dns_models.Domain, "objects") as domains:
        domains.get_all.return_value = []
        for _ in range(2):
            with pytest.raises(RuntimeError):
                service._sync_all_domains("https://eco", "realm", "secret")
    assert service._client.post.call_count == 2
    assert service._last_full_sync_at == previous
    assert service._last_sync_dt is None


def test_custom_record_updates_trigger_sync(service):
    custom_record = SimpleNamespace(
        domain=SimpleNamespace(realm_id=None, sync_to_ecosystem=True)
    )
    with mock.patch.object(dns_sync.dns_models.Record, "objects") as records:
        records.get_all.return_value = [custom_record]
        assert service._has_recent_changes(mock.sentinel.since)
        custom_record.domain.sync_to_ecosystem = False
        assert not service._has_recent_changes(mock.sentinel.since)


def test_custom_zone_uses_existing_model_and_schema(service):
    from uuid import uuid4

    import netaddr

    domain = dns_sync.dns_models.Domain.restore(
        id=1,
        uuid=uuid4(),
        name="slavoniy.space",
        project_id=c.ZERO_UUID,
        sync_to_ecosystem=True,
        sync_only=False,
        realm_id=None,
    )
    source = dns_sync.dns_models.Record(
        uuid=uuid4(),
        domain=domain,
        project_id=c.ZERO_UUID,
        type="A",
        record=dns_sync.dns_models.ARecord(
            name="", address=netaddr.IPAddress("155.212.162.155")
        ),
    )
    service._client = mock.Mock()
    with (
        mock.patch.object(dns_sync.dns_models.Domain, "objects") as domains,
        mock.patch.object(dns_sync.dns_models.Record, "objects") as records,
    ):
        domains.get_all.return_value = [domain]
        records.get_all.return_value = [source]
        service._sync_custom_zones(
            "https://eco", "realm", mock.sentinel.auth, [domain]
        )
    [zone] = service._client.post.call_args.kwargs["json"]["zones"]
    assert zone["name"] == "slavoniy.space"
    [published] = zone["records"]
    assert published["full_name"] == "slavoniy.space"
    assert published["record"] == {
        "kind": "A",
        "name": "@",
        "address": "155.212.162.155",
    }


@pytest.mark.parametrize("initialization", ["existing", "create", "legacy"])
def test_sync_reads_opted_in_domains_once(service, initialization):
    scoped = SimpleNamespace(uuid="scoped", name="exordos.io", realm_id="a5f3957")
    custom = SimpleNamespace(uuid="custom", name="slavoniy.space", realm_id=None)
    legacy = mock.Mock(
        uuid="legacy", realm_id=None, sync_only=False, sync_to_ecosystem=True
    )
    snapshot = [custom]
    if initialization == "existing":
        snapshot.append(scoped)
    elif initialization == "legacy":
        # Separate ORM instances can represent the same row.
        snapshot.append(SimpleNamespace(uuid="legacy", realm_id=None))
    service._client = mock.Mock()
    service._domain_records = mock.Mock(return_value=[])
    service._eco_get_realm = mock.Mock(
        return_value={"realm_id": "a5f3957", "domain": "a5f3957.exordos.io"}
    )
    with (
        mock.patch.object(dns_sync.dns_models, "Domain") as domains,
        mock.patch.object(dns_sync.dns_models.Record, "objects") as records,
    ):
        domains.objects.get_all.return_value = snapshot
        domains.objects.get_one_or_none.side_effect = [
            None,
            legacy if initialization == "legacy" else None,
        ]
        created = mock.Mock(realm_id="a5f3957")
        created.name = "exordos.io"
        domains.return_value = created
        records.get_all.return_value = []

        for _ in range(2):
            domains.objects.get_all.reset_mock()
            service._last_sync_dt = None
            service._sync_all_domains("https://eco", "realm", "secret")
            domains.objects.get_all.assert_called_once()
            assert (
                domains.objects.get_all.call_args.kwargs["filters"][
                    "sync_to_ecosystem"
                ].value
                is True
            )

    payloads = [call.kwargs["json"] for call in service._client.post.call_args_list]
    assert (
        payloads
        == [
            {"domain": {"name": "exordos.io", "realm_id": "a5f3957"}, "records": []},
            {"zones": [{"name": "slavoniy.space", "records": []}]},
        ]
        * 2
    )


def test_idle_sync_does_not_load_domains(service):
    service._initialized = True
    service._last_sync_dt = mock.sentinel.last
    service._last_full_sync_at = 100
    service._has_recent_changes = mock.Mock(return_value=False)
    with (
        mock.patch.object(dns_sync.time, "monotonic", return_value=101),
        mock.patch.object(dns_sync.dns_models.Domain, "objects") as domains,
    ):
        service._sync_all_domains("https://eco", "realm", "secret")
    domains.get_all.assert_not_called()
