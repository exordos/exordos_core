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

"""A DNS record carries who created it, and only the server says who.

The tag is what lets a realm's DNS mirror tell its own rows from the ones
this installation publishes into the same zone, so a client must not be
able to write it -- otherwise anybody could hand a row to somebody else's
reconciler and have it removed.
"""

import typing as tp
import uuid as sys_uuid

from gcl_iam.tests.functional import clients as iam_clients
import pytest

from exordos_core.common import constants as c


def _reserved(tags):
    return [tag for tag in tags if tag.startswith(c.TAG_RESERVED_PREFIX)]


class TestRecordTags:
    @pytest.fixture()
    def domain(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["dns", "domains"])
        response = client.post(
            url,
            json={
                "uuid": str(sys_uuid.uuid4()),
                "name": "tags.test",
                "project_id": str(c.ZERO_UUID),
            },
        )
        assert response.status_code == 201, response.text
        yield response.json()

    def _record(self, client, domain, **kwargs):
        url = client.build_collection_uri(["dns", "domains", domain["uuid"], "records"])
        data = {
            "uuid": str(sys_uuid.uuid4()),
            "project_id": str(c.ZERO_UUID),
            "type": "A",
            "ttl": 300,
            "record": {"kind": "A", "name": "www", "address": "1.2.3.4"},
        }
        data.update(kwargs)
        response = client.post(url, json=data)
        assert response.status_code == 201, response.text
        return response.json()

    def test_a_record_is_owned_by_whoever_created_it(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        domain: tp.Dict,
    ):
        client = user_api_client(auth_user_admin)

        record = self._record(client, domain)

        assert len(_reserved(record["tags"])) == 1
        assert record["tags"][0].startswith(c.TAG_OWNER_USER_PREFIX)

    def test_the_client_keeps_its_own_tags_beside_the_owner(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        domain: tp.Dict,
    ):
        client = user_api_client(auth_user_admin)

        record = self._record(client, domain, tags=["env:prod"])

        assert "env:prod" in record["tags"]
        assert len(_reserved(record["tags"])) == 1

    def test_an_owner_the_client_asks_for_is_refused(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        domain: tp.Dict,
    ):
        client = user_api_client(auth_user_admin)
        forged = c.owner_user_tag(sys_uuid.uuid4())

        record = self._record(client, domain, tags=[forged, "env:prod"])

        assert forged not in record["tags"]
        assert record["tags"] == ["env:prod"] + _reserved(record["tags"])

    def test_an_update_cannot_take_a_record_over(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        domain: tp.Dict,
    ):
        client = user_api_client(auth_user_admin)
        record = self._record(client, domain, tags=["env:prod"])
        owner = _reserved(record["tags"])
        url = client.build_resource_uri(
            ["dns", "domains", domain["uuid"], "records", record["uuid"]]
        )

        response = client.put(
            url,
            json={"tags": [c.owner_user_tag(sys_uuid.uuid4()), "env:stage"]},
        )

        assert response.status_code == 200, response.text
        assert _reserved(response.json()["tags"]) == owner
        assert "env:stage" in response.json()["tags"]

    def test_an_update_cannot_orphan_a_record(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        domain: tp.Dict,
    ):
        client = user_api_client(auth_user_admin)
        record = self._record(client, domain)
        owner = _reserved(record["tags"])
        url = client.build_resource_uri(
            ["dns", "domains", domain["uuid"], "records", record["uuid"]]
        )

        response = client.put(url, json={"tags": []})

        assert response.status_code == 200, response.text
        assert _reserved(response.json()["tags"]) == owner

    def test_a_write_claims_a_record_nobody_owns(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        domain: tp.Dict,
    ):
        """A record written before tags existed becomes the writer's.

        Otherwise the mirror never sees it among its own: it recreates
        it, is answered with a conflict, updates, and finds it missing
        again on the next pass -- and never removes it either. The zone's
        SOA stands in for such a record: the installation writes it, so
        no caller owns it.
        """
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["dns", "domains", domain["uuid"], "records"])
        soa = [r for r in client.get(url).json() if r["type"] == "SOA"][0]
        assert _reserved(soa["tags"]) == []

        response = client.put(
            client.build_resource_uri(
                ["dns", "domains", domain["uuid"], "records", soa["uuid"]]
            ),
            json={"ttl": 4242},
        )

        assert response.status_code == 200, response.text
        assert len(_reserved(response.json()["tags"])) == 1

    def test_a_zone_can_be_asked_for_one_owners_records(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        domain: tp.Dict,
    ):
        """What a realm's mirror asks, instead of reading the whole zone."""
        client = user_api_client(auth_user_admin)
        mine = self._record(client, domain)
        owner = _reserved(mine["tags"])[0]
        url = client.build_collection_uri(["dns", "domains", domain["uuid"], "records"])

        response = client.get(url, params={"q": 'tags:"%s"' % owner})

        assert response.status_code == 200, response.text
        returned = {r["uuid"] for r in response.json()}
        assert mine["uuid"] in returned
        # The SOA the zone is created with belongs to nobody.
        assert all(owner in r["tags"] for r in response.json())

    def test_another_owners_records_are_not_returned(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        domain: tp.Dict,
    ):
        client = user_api_client(auth_user_admin)
        self._record(client, domain)
        url = client.build_collection_uri(["dns", "domains", domain["uuid"], "records"])
        somebody_else = c.owner_user_tag(sys_uuid.uuid4())

        response = client.get(url, params={"q": 'tags:"%s"' % somebody_else})

        assert response.status_code == 200, response.text
        assert response.json() == []
