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

import pytest

from bazooka import exceptions as bazooka_exc

from exordos_core.common import constants as c
from exordos_core.dns_sync import service


ENDPOINT = "http://ecosystem.test"
HEADERS = {"Authorization": "Bearer token"}
DOMAIN_UUID = "d0000000-0000-0000-0000-000000000001"
MIRROR_USER = sys_uuid.UUID("11111111-1111-1111-1111-111111111111")
ECOSYSTEM_USER = sys_uuid.UUID("22222222-2222-2222-2222-222222222222")


def _bad_request():
    return bazooka_exc.BadRequestError(mock.Mock(status_code=400))


def _conflict():
    return bazooka_exc.ConflictError(mock.Mock(status_code=409))


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
    sync._filter_lang_refused = False
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
        sync._full_sync_domain(domain, ENDPOINT, HEADERS, DOMAIN_UUID)


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
    def test_the_owner_is_asked_of_the_installation_that_stamps_it(self, dns_sync):
        dns_sync._owner_tag = None
        dns_sync._client.get.return_value.json.return_value = {
            "user_info": {"uuid": str(MIRROR_USER)},
        }

        tag = dns_sync._eco_owner_tag(ENDPOINT, HEADERS)

        assert tag == c.owner_user_tag(MIRROR_USER)
        url = dns_sync._client.get.call_args[0][0]
        # The path segment is the route attribute name, `introspect` --
        # the docstring next to it in the core says "introspection", and
        # that spelling 404s (measured on a live core).
        assert url.endswith(
            "/v1/iam/clients/%s/actions/introspect" % c.DEFAULT_CLIENT_UUID
        )

    def test_the_answer_is_asked_for_once(self, dns_sync):
        dns_sync._owner_tag = None
        dns_sync._client.get.return_value.json.return_value = {
            "user_info": {"uuid": str(MIRROR_USER)},
        }

        dns_sync._eco_owner_tag(ENDPOINT, HEADERS)
        dns_sync._eco_owner_tag(ENDPOINT, HEADERS)

        assert dns_sync._client.get.call_count == 1

    def test_an_answer_without_a_subject_is_no_owner(self, dns_sync):
        dns_sync._owner_tag = None
        dns_sync._client.get.return_value.json.return_value = {}

        with mock.patch.object(service, "LOG"):
            assert dns_sync._eco_owner_tag(ENDPOINT, HEADERS) is None


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

    def test_an_ecosystem_that_refuses_the_filter_is_asked_once(self, dns_sync):
        # An older ecosystem knows no `q`: fall back, and stop asking.
        ok = mock.Mock()
        ok.json.return_value = [_eco_record("a1", "www", [])]
        dns_sync._client.get.side_effect = [_bad_request(), ok, ok]

        with mock.patch.object(service, "LOG"):
            first = dns_sync._eco_list_records(
                ENDPOINT, HEADERS, DOMAIN_UUID, dns_sync._owner_tag
            )
            second = dns_sync._eco_list_records(
                ENDPOINT, HEADERS, DOMAIN_UUID, dns_sync._owner_tag
            )

        assert first == second == ok.json.return_value
        assert dns_sync._filter_lang_refused
        # Three calls: the refused one, its fallback, and the second read
        # which does not try the filter again.
        assert dns_sync._client.get.call_count == 3
        assert "params" not in dns_sync._client.get.call_args[1]


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
