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

"""A DNS record carries tags, and a zone can be asked for the tagged ones.

A realm's DNS mirror marks the records it writes into a shared zone and
reads back only those, so it can tell its rows from the ones this
installation publishes into the same zone.
"""

import typing as tp
import uuid as sys_uuid

from gcl_iam.tests.functional import clients as iam_clients
import pytest

from exordos_core.common import constants as c
from exordos_core.dns_sync import service as dns_sync


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

    def test_a_record_keeps_the_tags_it_is_written_with(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        domain: tp.Dict,
    ):
        client = user_api_client(auth_user_admin)
        mark = dns_sync.realm_tag(sys_uuid.uuid4())

        record = self._record(client, domain, tags=["env:prod", mark])

        assert record["tags"] == ["env:prod", mark]

    def test_a_record_written_without_tags_has_none(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        domain: tp.Dict,
    ):
        client = user_api_client(auth_user_admin)

        record = self._record(client, domain)

        assert record["tags"] == []

    def test_an_update_marks_a_record(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        domain: tp.Dict,
    ):
        """What the mirror does to a row it wrote before it marked them."""
        client = user_api_client(auth_user_admin)
        record = self._record(client, domain)
        mark = dns_sync.realm_tag(sys_uuid.uuid4())
        url = client.build_resource_uri(
            ["dns", "domains", domain["uuid"], "records", record["uuid"]]
        )

        response = client.put(url, json={"tags": [mark]})

        assert response.status_code == 200, response.text
        assert response.json()["tags"] == [mark]

    def test_an_update_without_tags_leaves_them(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        domain: tp.Dict,
    ):
        client = user_api_client(auth_user_admin)
        mark = dns_sync.realm_tag(sys_uuid.uuid4())
        record = self._record(client, domain, tags=[mark])
        url = client.build_resource_uri(
            ["dns", "domains", domain["uuid"], "records", record["uuid"]]
        )

        response = client.put(url, json={"ttl": 4242})

        assert response.status_code == 200, response.text
        assert response.json()["tags"] == [mark]

    def test_a_zone_can_be_asked_for_one_realms_records(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        domain: tp.Dict,
    ):
        """What a realm's mirror asks, instead of reading the whole zone."""
        client = user_api_client(auth_user_admin)
        mark = dns_sync.realm_tag(sys_uuid.uuid4())
        mine = self._record(client, domain, tags=[mark])
        self._record(client, domain, tags=[dns_sync.realm_tag(sys_uuid.uuid4())])
        self._record(client, domain)
        url = client.build_collection_uri(["dns", "domains", domain["uuid"], "records"])

        response = client.get(url, params={"q": 'tags:"%s"' % mark})

        assert response.status_code == 200, response.text
        assert [r["uuid"] for r in response.json()] == [mine["uuid"]]

    def test_another_realms_records_are_not_returned(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        domain: tp.Dict,
    ):
        client = user_api_client(auth_user_admin)
        self._record(client, domain, tags=[dns_sync.realm_tag(sys_uuid.uuid4())])
        url = client.build_collection_uri(["dns", "domains", domain["uuid"], "records"])
        somebody_else = dns_sync.realm_tag(sys_uuid.uuid4())

        response = client.get(url, params={"q": 'tags:"%s"' % somebody_else})

        assert response.status_code == 200, response.text
        assert response.json() == []
