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
those within a minute of their creation.
"""

from unittest import mock
import uuid as sys_uuid

from bazooka import exceptions as bazooka_exc
import jwt
import pytest

from exordos_core.common import constants as c
from exordos_core.dns_sync import service

ENDPOINT = "http://ecosystem.test"
HEADERS = {"Authorization": "Bearer token"}
DOMAIN_UUID = "d0000000-0000-0000-0000-000000000001"
MIRROR_USER = sys_uuid.UUID("11111111-1111-1111-1111-111111111111")
ECOSYSTEM_USER = sys_uuid.UUID("22222222-2222-2222-2222-222222222222")


def _token(sub, aud="core"):
    """An access token as the ecosystem's IAM issues one.

    Signed with a key nobody here holds: the mirror only reads its own
    token, and reading is all `_realm_owner_tag` does.
    """
    claims = {"aud": aud, "jti": str(sys_uuid.uuid4()), "typ": "Bearer"}
    if sub is not None:
        claims["sub"] = str(sub)
    return jwt.encode(claims, "not-the-verifying-key", algorithm="HS256")


def _http_error(status):
    cause = mock.Mock()
    cause.response.status_code = status
    if status == 400:
        return bazooka_exc.BadRequestError(cause)
    return bazooka_exc.BaseHTTPException(cause)


def _bad_request():
    return _http_error(400)


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


@pytest.fixture
def dns_sync():
    sync = service.DNSSyncService.__new__(service.DNSSyncService)
    sync._client = mock.Mock()
    sync._owner_tag = c.owner_user_tag(MIRROR_USER)
    sync._owner_token = _token(MIRROR_USER)
    sync._filter_lang_refused = set()
    sync._eco_create_record = mock.Mock()
    sync._eco_update_record = mock.Mock()
    sync._eco_delete_record = mock.Mock()
    return sync


@pytest.fixture
def domain():
    zone = mock.Mock()
    zone.name = "child.exordos.io"
    return zone


def _run(sync, domain, eco_records, local_records=()):
    # `objects` hands out a manager per access, so the model is what a test
    # can hold still.
    record_model = mock.Mock()
    record_model.objects.get_all.return_value = list(local_records)
    with (
        mock.patch.object(service.dns_models, "Record", record_model),
        mock.patch.object(service, "LOG"),
    ):
        sync._eco_list_records = mock.Mock(return_value=eco_records)
        sync._full_sync_domain(domain, ENDPOINT, HEADERS, DOMAIN_UUID, sync._owner_tag)


class TestFullSyncDeletes:
    def test_a_record_this_mirror_wrote_is_removed_when_it_is_gone_locally(
        self, dns_sync, domain
    ):
        mine = _eco_record("a1", "www", [c.owner_user_tag(MIRROR_USER)])

        _run(dns_sync, domain, [mine])

        dns_sync._eco_delete_record.assert_called_once_with(
            ENDPOINT, HEADERS, DOMAIN_UUID, "a1"
        )

    def test_a_record_of_another_owner_is_left_alone(self, dns_sync, domain):
        theirs = _eco_record("a2", "app", [c.owner_user_tag(ECOSYSTEM_USER)])

        _run(dns_sync, domain, [theirs])

        dns_sync._eco_delete_record.assert_not_called()

    def test_a_record_with_no_owner_is_left_alone(self, dns_sync, domain):
        # Rows that predate the tags: unknown owner reads as "not mine".
        older = _eco_record("a3", "legacy", [])

        _run(dns_sync, domain, [older])

        dns_sync._eco_delete_record.assert_not_called()

    def test_nothing_is_removed_while_the_owner_is_unknown(self, dns_sync, domain):
        # Introspection failed or carries no subject; creating and updating
        # go on, removing does not.
        dns_sync._owner_tag = None
        mine = _eco_record("a4", "www", [c.owner_user_tag(MIRROR_USER)])

        _run(dns_sync, domain, [mine])

        dns_sync._eco_delete_record.assert_not_called()

    def test_a_record_that_is_still_local_is_not_removed(self, dns_sync, domain):
        # The zone's copy of a row this mirror still has is what the
        # mirror is for; only what is gone locally is a candidate.
        mine = _eco_record("a1", "www", [c.owner_user_tag(MIRROR_USER)])
        local = mock.Mock(uuid="a1")
        dns_sync._build_record_data = mock.Mock(
            return_value={
                k: mine[k] for k in ("uuid", "type", "ttl", "disabled", "record")
            }
        )

        _run(dns_sync, domain, [mine], local_records=[local])

        dns_sync._eco_delete_record.assert_not_called()
        # Same on both sides: nothing to write either.
        dns_sync._eco_create_record.assert_not_called()
        dns_sync._eco_update_record.assert_not_called()

    def test_the_mirror_sorts_a_mixed_zone(self, dns_sync, domain):
        records = [
            _eco_record("a1", "www", [c.owner_user_tag(MIRROR_USER)]),
            _eco_record("a2", "app", [c.owner_user_tag(ECOSYSTEM_USER)]),
            _eco_record("a3", "legacy", []),
        ]

        _run(dns_sync, domain, records)

        assert dns_sync._eco_delete_record.call_count == 1
        assert dns_sync._eco_delete_record.call_args[0][3] == "a1"


class TestOwnerTag:
    """Who this mirror is, read out of the token it writes with."""

    def test_the_owner_is_read_out_of_the_token(self, dns_sync):
        dns_sync._owner_tag = None
        dns_sync._owner_token = None

        with mock.patch.object(service, "LOG"):
            tag = dns_sync._realm_owner_tag(_token(MIRROR_USER))

        assert tag == c.owner_user_tag(MIRROR_USER)

    def test_nobody_is_asked_who_this_realm_is(self, dns_sync):
        # The token carries the subject the ecosystem would report, so
        # there is nothing to ask it -- and nothing to retry when it is
        # an older ecosystem with no such endpoint to ask.
        dns_sync._owner_tag = None
        dns_sync._owner_token = None

        with mock.patch.object(service, "LOG"):
            dns_sync._realm_owner_tag(_token(MIRROR_USER))

        dns_sync._client.get.assert_not_called()

    def test_a_token_without_a_subject_is_no_owner(self, dns_sync):
        dns_sync._owner_tag = None
        dns_sync._owner_token = None

        with mock.patch.object(service, "LOG"):
            assert dns_sync._realm_owner_tag(_token(None)) is None

    def test_a_token_that_does_not_parse_owns_nothing(self, dns_sync):
        # Without an owner the mirror stops removing anything at all,
        # which is the safe half to lose.
        dns_sync._owner_tag = None
        dns_sync._owner_token = None

        with mock.patch.object(service, "LOG"):
            assert dns_sync._realm_owner_tag("not-a-token") is None

        assert dns_sync._owner_tag is None

    def test_an_unreadable_token_is_reported_once(self, dns_sync):
        # Remembered as "no owner" so a broken token does not fill the
        # log once a minute for as long as it stays broken.
        dns_sync._owner_tag = None
        dns_sync._owner_token = None

        with mock.patch.object(service, "LOG") as log:
            dns_sync._realm_owner_tag("not-a-token")
            dns_sync._realm_owner_tag("not-a-token")

        assert log.exception.call_count == 1

    def test_a_new_token_is_a_new_subject_to_be(self, dns_sync):
        # ValuesStore can hand out another token without this service
        # restarting; the tag decides what is removed, so it is read
        # again rather than carried over.
        with mock.patch.object(service, "LOG"):
            tag = dns_sync._realm_owner_tag(_token(ECOSYSTEM_USER))

        assert tag == c.owner_user_tag(ECOSYSTEM_USER)


class TestAskingForItsOwnRecords:
    """A zone is read every minute; only the mirror's own rows are its business."""

    def test_the_zone_is_asked_for_this_mirrors_records(self, dns_sync):
        dns_sync._client.get.return_value.json.return_value = []

        dns_sync._eco_list_records(ENDPOINT, HEADERS, DOMAIN_UUID, dns_sync._owner_tag)

        params = dns_sync._client.get.call_args[1]["params"]
        assert params == {"q": 'tags:"%s"' % dns_sync._owner_tag}

    def test_without_an_owner_the_whole_zone_is_read(self, dns_sync):
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
        dns_sync._client.get.side_effect = [_http_error(status), ok, ok]

        with mock.patch.object(service, "LOG"):
            first = dns_sync._eco_list_records(
                ENDPOINT, HEADERS, DOMAIN_UUID, dns_sync._owner_tag
            )
            second = dns_sync._eco_list_records(
                ENDPOINT, HEADERS, DOMAIN_UUID, dns_sync._owner_tag
            )

        assert first == second == ok.json.return_value
        assert ENDPOINT in dns_sync._filter_lang_refused
        # Three calls: the refused one, its fallback, and the second read
        # which does not try the filter again.
        assert dns_sync._client.get.call_count == 3
        assert "params" not in dns_sync._client.get.call_args[1]

    @pytest.mark.parametrize("status", (401, 403, 404, 502, 503))
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
            dns_sync._eco_list_records(
                ENDPOINT, HEADERS, DOMAIN_UUID, dns_sync._owner_tag
            )

        assert ENDPOINT not in dns_sync._filter_lang_refused
        assert dns_sync._client.get.call_count == 1


class TestCreatingWhatIsAlreadyThere:
    def test_a_record_that_cannot_be_seen_is_updated_not_reported(self, dns_sync):
        # Asking for its own records hides anything written before records
        # carried an owner; creating that again is an update.
        dns_sync._eco_update_record = mock.Mock()
        dns_sync._client.post.side_effect = _conflict()
        data = {"uuid": "a1", "type": "A", "ttl": 300}

        # The fixture stands in for the HTTP helpers; this is the one
        # under test, so it is the real one that runs.
        service.DNSSyncService._eco_create_record(
            dns_sync, ENDPOINT, HEADERS, DOMAIN_UUID, data
        )

        dns_sync._eco_update_record.assert_called_once_with(
            ENDPOINT, HEADERS, DOMAIN_UUID, "a1", data
        )
