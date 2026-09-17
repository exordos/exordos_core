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

"""What the realm's DNS mirror is allowed to remove upstream.

The zone a managed realm mirrors is not the realm's alone: the ecosystem
publishes the realm's ingress records into the same zone, and an operator
may add more. Reconciling it as "delete whatever I do not have" removed
those within a minute of their creation, so the mirror marks what it
writes and removes only what carries its mark.
"""

from unittest import mock

from bazooka import exceptions as bazooka_exc
import pytest

from exordos_core.dns_sync import service

ENDPOINT = "http://ecosystem.test"
HEADERS = {"Authorization": "Bearer token"}
DOMAIN_UUID = "d0000000-0000-0000-0000-000000000001"
MINE = service.realm_tag("11111111-1111-1111-1111-111111111111")
OTHER_REALM = service.realm_tag("22222222-2222-2222-2222-222222222222")


def _http_error(status, body=None):
    cause = mock.Mock()
    cause.response.status_code = status
    cause.response.json.return_value = body
    if status == 400:
        return bazooka_exc.BadRequestError(cause)
    return bazooka_exc.BaseHTTPException(cause)


def _conflict():
    cause = mock.Mock()
    cause.response.status_code = 409
    return bazooka_exc.ConflictError(cause)


def _eco_record(uuid, name, tags):
    return {
        "uuid": uuid,
        "type": "A",
        "ttl": 300,
        "disabled": False,
        "record": {"kind": "A", "name": name, "address": "10.0.0.1"},
        "tags": tags,
    }


def _local_record(uuid, name, tags=()):
    rec = mock.Mock(uuid=uuid, type="A", ttl=300, disabled=False, tags=list(tags))
    rec.name = name
    rec.record = {"kind": "A", "name": name, "address": "10.0.0.1"}
    record_type = mock.Mock()
    record_type.to_simple_type.side_effect = lambda value: value
    rec.properties.properties = {"record": mock.Mock()}
    rec.properties.properties["record"].get_property_type.return_value = record_type
    return rec


@pytest.fixture
def dns_sync():
    sync = service.DNSSyncService.__new__(service.DNSSyncService)
    sync._client = mock.Mock()
    sync._filter_lang_refused = {}
    sync._eco_create_record = mock.Mock()
    sync._eco_update_record = mock.Mock()
    sync._eco_delete_record = mock.Mock()
    return sync


@pytest.fixture
def domain():
    zone = mock.Mock()
    zone.name = "child.exordos.io"
    return zone


def _run(sync, domain, eco_records, local_records=(), refused=False):
    # `objects` hands out a manager per access, so the model is what a test
    # can hold still.
    record_model = mock.Mock()
    record_model.objects.get_all.return_value = list(local_records)

    def list_records(endpoint, *args):
        if refused:
            sync._filter_lang_refused[endpoint] = 0
        return eco_records

    with (
        mock.patch.object(service.dns_models, "Record", record_model),
        mock.patch.object(service, "LOG"),
    ):
        sync._eco_list_records = mock.Mock(side_effect=list_records)
        sync._full_sync_domain(domain, ENDPOINT, HEADERS, DOMAIN_UUID, MINE)


class TestFullSyncDeletes:
    def test_a_record_this_mirror_wrote_is_removed_when_it_is_gone_locally(
        self, dns_sync, domain
    ):
        mine = _eco_record("a1", "www", [MINE])

        _run(dns_sync, domain, [mine])

        dns_sync._eco_delete_record.assert_called_once_with(
            ENDPOINT, HEADERS, DOMAIN_UUID, "a1"
        )

    def test_a_record_of_another_realm_is_left_alone(self, dns_sync, domain):
        # Two realms of one project write as the same IAM user; the mark
        # is the realm's, so they still keep apart.
        theirs = _eco_record("a2", "app", [OTHER_REALM])

        _run(dns_sync, domain, [theirs])

        dns_sync._eco_delete_record.assert_not_called()

    def test_an_unmarked_record_is_left_alone(self, dns_sync, domain):
        # The ecosystem's own records, an operator's, and rows written
        # before the mirror marked them.
        unmarked = _eco_record("a3", "legacy", ["env:prod"])

        _run(dns_sync, domain, [unmarked])

        dns_sync._eco_delete_record.assert_not_called()

    def test_a_record_marked_for_multiple_realms_is_left_alone(self, dns_sync, domain):
        record = _eco_record("a1", "www", [MINE, OTHER_REALM])
        _run(dns_sync, domain, [record])
        dns_sync._eco_delete_record.assert_not_called()

    def test_a_record_that_is_still_local_is_not_touched(self, dns_sync, domain):
        mine = _eco_record("a1", "www", ["env:prod", MINE])
        local = _local_record("a1", "www", tags=["env:prod"])

        _run(dns_sync, domain, [mine], local_records=[local])

        dns_sync._eco_delete_record.assert_not_called()
        dns_sync._eco_create_record.assert_not_called()
        dns_sync._eco_update_record.assert_not_called()

    def test_the_mirror_sorts_a_mixed_zone(self, dns_sync, domain):
        records = [
            _eco_record("a1", "www", [MINE]),
            _eco_record("a2", "app", [OTHER_REALM]),
            _eco_record("a3", "legacy", []),
        ]

        _run(dns_sync, domain, records)

        assert dns_sync._eco_delete_record.call_count == 1
        assert dns_sync._eco_delete_record.call_args[0][3] == "a1"

    def test_nothing_is_removed_where_records_cannot_be_marked(self, dns_sync, domain):
        # An ecosystem that refuses the tag filter has no tags on records:
        # nothing there carries a mark, so nothing is this mirror's to
        # remove.
        mine = _eco_record("a1", "www", [MINE])

        _run(dns_sync, domain, [mine], refused=True)

        dns_sync._eco_delete_record.assert_not_called()


class TestMarking:
    def test_a_created_record_carries_the_local_tags_and_the_mark(
        self, dns_sync, domain
    ):
        local = _local_record("a1", "www", tags=["env:prod"])

        _run(dns_sync, domain, [], local_records=[local])

        data = dns_sync._eco_create_record.call_args[0][3]
        assert data["tags"] == ["env:prod", MINE]

    def test_an_unmarked_copy_is_not_claimed_by_uuid(self, dns_sync, domain):
        copy = _eco_record("a1", "www", [])
        local = _local_record("a1", "www")

        _run(dns_sync, domain, [copy], local_records=[local])

        dns_sync._eco_update_record.assert_not_called()

    def test_no_tags_are_sent_where_records_cannot_hold_them(self, dns_sync, domain):
        local = _local_record("a1", "www", tags=["env:prod"])

        _run(dns_sync, domain, [], local_records=[local], refused=True)

        data = dns_sync._eco_create_record.call_args[0][3]
        assert "tags" not in data

    def test_the_mark_is_not_doubled(self):
        local = _local_record("a1", "www", tags=[MINE, "env:prod"])

        data = service.DNSSyncService._build_record_data(local, MINE)

        assert data["tags"] == ["env:prod", MINE]

    def test_local_tags_cannot_select_another_realms_owner(self):
        local = _local_record("a1", "www", tags=[OTHER_REALM, "env:prod"])
        data = service.DNSSyncService._build_record_data(local, MINE)
        assert data["tags"] == ["env:prod", MINE]


class TestAskingForItsOwnRecords:
    """A zone is read every minute; only the mirror's own rows are its business."""

    def test_the_zone_is_asked_for_this_mirrors_records(self, dns_sync):
        dns_sync._client.get.return_value.json.return_value = []

        dns_sync._eco_list_records(ENDPOINT, HEADERS, DOMAIN_UUID, MINE)

        params = dns_sync._client.get.call_args[1]["params"]
        assert params == {"q": 'tags:"%s"' % MINE}

    def test_without_a_mark_the_whole_zone_is_read(self, dns_sync):
        dns_sync._client.get.return_value.json.return_value = []

        dns_sync._eco_list_records(ENDPOINT, HEADERS, DOMAIN_UUID, None)

        assert "params" not in dns_sync._client.get.call_args[1]

    @pytest.mark.parametrize("status", sorted(service.FILTER_UNSUPPORTED_STATUSES))
    def test_an_ecosystem_that_refuses_the_filter_is_asked_once(self, dns_sync, status):
        # An ecosystem that cannot read `q` refuses it two ways: the
        # current one rejects the expression as a bad request, and one
        # that predates it takes `q` for a field of the resource, finds
        # no such field and fails with a 500. Both mean the same thing --
        # fall back to the whole zone, and stop asking.
        ok = mock.Mock()
        ok.json.return_value = [_eco_record("a1", "www", [])]
        body = {"type": "KeyError", "message": "'q'"} if status == 500 else None
        dns_sync._client.get.side_effect = [_http_error(status, body), ok, ok]

        with mock.patch.object(service, "LOG"):
            first = dns_sync._eco_list_records(ENDPOINT, HEADERS, DOMAIN_UUID, MINE)
            second = dns_sync._eco_list_records(ENDPOINT, HEADERS, DOMAIN_UUID, MINE)

        assert first == second == ok.json.return_value
        assert ENDPOINT in dns_sync._filter_lang_refused
        # Three calls: the refused one, its fallback, and the second read
        # which does not try the filter again.
        assert dns_sync._client.get.call_count == 3
        assert "params" not in dns_sync._client.get.call_args[1]

    def test_a_refusal_is_forgotten_in_time(self, dns_sync):
        # An ecosystem upgraded in place under a running mirror learns the
        # filter; the mirror finds out on the next try after the refusal
        # has aged, and marks its records from then on.
        dns_sync._client.get.return_value.json.return_value = []
        dns_sync._filter_lang_refused[ENDPOINT] = 0

        with mock.patch.object(
            service.time, "monotonic", return_value=service.FILTER_REFUSAL_TTL
        ):
            dns_sync._eco_list_records(ENDPOINT, HEADERS, DOMAIN_UUID, MINE)

        assert dns_sync._client.get.call_args[1]["params"] == {"q": 'tags:"%s"' % MINE}
        assert ENDPOINT not in dns_sync._filter_lang_refused
        assert dns_sync._mark_for(ENDPOINT, MINE) == MINE

    @pytest.mark.parametrize("status", (401, 403, 404, 500, 502, 503))
    def test_a_failure_that_is_not_a_refusal_is_raised(self, dns_sync, status):
        # A token that stopped working or a gateway that is briefly down
        # says nothing about the filter. Reading the zone again would
        # fail the same way, so the failure is the caller's to see -- and
        # the endpoint is not written off as unable to filter, which
        # would outlive the outage that caused it.
        dns_sync._client.get.side_effect = _http_error(status)

        with (
            mock.patch.object(service, "LOG"),
            pytest.raises(bazooka_exc.BaseHTTPException),
        ):
            dns_sync._eco_list_records(ENDPOINT, HEADERS, DOMAIN_UUID, MINE)

        assert ENDPOINT not in dns_sync._filter_lang_refused
        assert dns_sync._client.get.call_count == 1

    @pytest.mark.parametrize(
        "body", [{"type": "KeyError", "message": "'other'"}, "server error"]
    )
    def test_other_server_errors_are_not_filter_refusals(self, dns_sync, body):
        dns_sync._client.get.side_effect = _http_error(500, body)
        with pytest.raises(bazooka_exc.BaseHTTPException):
            dns_sync._eco_list_records(ENDPOINT, HEADERS, DOMAIN_UUID, MINE)
        assert not dns_sync._filter_lang_refused
        assert dns_sync._client.get.call_count == 1

    def test_non_json_server_errors_are_not_filter_refusals(self, dns_sync):
        error = _http_error(500)
        error.cause.response.json.side_effect = ValueError("not JSON")
        dns_sync._client.get.side_effect = error
        with pytest.raises(bazooka_exc.BaseHTTPException):
            dns_sync._eco_list_records(ENDPOINT, HEADERS, DOMAIN_UUID, MINE)
        assert not dns_sync._filter_lang_refused

    def test_failed_fallback_does_not_disable_filtering(self, dns_sync):
        dns_sync._client.get.side_effect = [_http_error(400), _http_error(503)]
        with pytest.raises(bazooka_exc.BaseHTTPException):
            dns_sync._eco_list_records(ENDPOINT, HEADERS, DOMAIN_UUID, MINE)
        assert not dns_sync._filter_lang_refused


class TestCreatingWhatIsAlreadyThere:
    @pytest.mark.parametrize("tags", ([], [OTHER_REALM], [MINE, OTHER_REALM]))
    def test_a_conflicting_foreign_record_is_not_updated(self, dns_sync, tags):
        dns_sync._eco_update_record = service.DNSSyncService._eco_update_record.__get__(
            dns_sync
        )
        dns_sync._client.post.side_effect = _conflict()
        data = _eco_record("a1", "www", [MINE])
        dns_sync._client.get.return_value.json.return_value = _eco_record(
            "a1", "www", tags
        )
        with pytest.raises(ValueError, match="owned"):
            service.DNSSyncService._eco_create_record(
                dns_sync, ENDPOINT, HEADERS, DOMAIN_UUID, data
            )
        dns_sync._client.put.assert_not_called()

    def test_a_conflicting_owned_record_is_updated(self, dns_sync):
        dns_sync._eco_update_record = service.DNSSyncService._eco_update_record.__get__(
            dns_sync
        )
        dns_sync._client.post.side_effect = _conflict()
        data = _eco_record("a1", "www", [MINE])
        dns_sync._client.get.return_value.json.return_value = data

        # The fixture stands in for the HTTP helpers; this is the one
        # under test, so it is the real one that runs.
        service.DNSSyncService._eco_create_record(
            dns_sync, ENDPOINT, HEADERS, DOMAIN_UUID, data
        )

        dns_sync._client.put.assert_called_once_with(
            f"{ENDPOINT}/api/core/v1/dns/domains/{DOMAIN_UUID}/records/a1",
            json=data,
            headers=HEADERS,
        )

    @pytest.mark.parametrize("tags", ([], [OTHER_REALM]))
    def test_direct_updates_cannot_claim_foreign_records(self, dns_sync, tags):
        data = _eco_record("a1", "www", [MINE])
        dns_sync._client.get.return_value.json.return_value = _eco_record(
            "a1", "www", tags
        )
        with pytest.raises(ValueError, match="owned"):
            service.DNSSyncService._eco_update_record(
                dns_sync, ENDPOINT, HEADERS, DOMAIN_UUID, "a1", data
            )
        dns_sync._client.put.assert_not_called()

    def test_updates_without_a_realm_mark_are_refused(self, dns_sync):
        data = _eco_record("a1", "www", [])
        with pytest.raises(ValueError, match="owned"):
            service.DNSSyncService._eco_update_record(
                dns_sync, ENDPOINT, HEADERS, DOMAIN_UUID, "a1", data
            )
        dns_sync._client.put.assert_not_called()
