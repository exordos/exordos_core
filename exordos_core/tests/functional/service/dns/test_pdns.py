#    Copyright 2025 Genesis Corporation.
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

import typing as tp
import uuid as sys_uuid

import dns.resolver
from gcl_iam.tests.functional import clients as iam_clients
import netaddr
import pytest

from exordos_core.common import constants as c
from exordos_core.user_api.dns.dm import models as dns_models

DEF_DOMAIN = "core.internal"


class TestDnsApi:
    # Utils

    @staticmethod
    def _cmp_shallow(
        left: tp.Dict[str, tp.Any],
        right: tp.Dict[str, tp.Any],
    ):
        return all((left[key] == right[key]) for key in left.keys())

    @pytest.fixture()
    def domain1(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        domain = {
            "uuid": str(sys_uuid.uuid4()),
            "name": DEF_DOMAIN,
            "project_id": str(c.ZERO_UUID),
        }
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["dns", "domains"])

        response = client.post(url, json=domain)
        output = response.json()

        assert response.status_code == 201
        assert self._cmp_shallow(domain, output)
        yield output

    @pytest.mark.xdist_group(name="pdns")
    def test_domains_list(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["dns", "domains"])

        response = client.get(url)

        assert response.status_code == 200
        assert len(response.json()) == 0

    @pytest.mark.xdist_group(name="pdns")
    def test_domains_add(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        domain1: tp.Dict,
        pdns_server: tp.Optional[int],
    ):
        client = user_api_client(auth_user_admin)

        # Check SOA Record

        url = client.build_collection_uri(
            ["dns", "domains", domain1["uuid"], "records"]
        )

        response = client.get(url)
        records = response.json()
        assert response.status_code == 200
        assert len(records) == 1
        assert records[0]["type"] == "SOA"
        assert records[0]["record"]["name"] == "@"
        assert (
            records[0]["record"]["primary_dns"] == "a.misconfigured.dns.server.invalid"
        )

        if pdns_server:
            res = dns.resolver.make_resolver_at("127.0.0.1", port=pdns_server)
            answer = res.resolve(DEF_DOMAIN, "SOA")

            assert len(answer) == 1
            assert (
                answer[0].to_text()
                == "a.misconfigured.dns.server.invalid. core.internal. 0 10800 3600 604800 3600"
            )

        # Delete

        url = client.build_resource_uri(["dns", "domains", domain1["uuid"]])

        response = client.delete(url)

        assert response.status_code == 204

        url = client.build_collection_uri(["dns", "domains"])

        response = client.get(url)

        assert response.status_code == 200
        assert len(response.json()) == 0

    @pytest.mark.xdist_group(name="pdns")
    def test_a_record(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        domain1: tp.Dict,
        pdns_server: tp.Optional[int],
    ):
        client = user_api_client(auth_user_admin)

        data = {
            "uuid": str(sys_uuid.uuid4()),
            "project_id": str(c.ZERO_UUID),
            "type": "A",
            "ttl": 0,
            "record": {"kind": "A", "name": "test", "address": "1.2.3.4"},
        }

        url = client.build_collection_uri(
            ["dns", "domains", domain1["uuid"], "records"]
        )

        response = client.post(url, json=data)
        output = response.json()

        assert response.status_code == 201
        assert self._cmp_shallow(data, output)
        assert output["record"]["name"] == "test"
        assert output["full_name"] == f"test.{DEF_DOMAIN}"

        url = client.build_resource_uri(
            ["dns", "domains", domain1["uuid"], "records", data["uuid"]]
        )

        response = client.get(url)
        record = response.json()
        assert response.status_code == 200
        assert self._cmp_shallow(data, record)

        if pdns_server:
            res = dns.resolver.make_resolver_at("127.0.0.1", port=pdns_server)
            answer = res.resolve(f"test.{DEF_DOMAIN}", "A")

            assert len(answer) == 1
            assert answer[0].address == "1.2.3.4"

        # Delete
        response = client.delete(url)

        assert response.status_code == 204

    @pytest.mark.xdist_group(name="pdns")
    def test_sync_only_realm_domain_is_hidden_from_powerdns_views(self, user_api):
        domain = dns_models.Domain(
            name="example.com",
            realm_id="a5f3957",
            sync_only=True,
            sync_to_ecosystem=True,
            project_id=c.ZERO_UUID,
        )
        domain.insert()
        record = dns_models.Record(
            domain=domain,
            type="A",
            record=dns_models.ARecord(
                name="workspace",
                address=netaddr.IPAddress("192.0.2.10"),
            ),
            project_id=c.ZERO_UUID,
        )
        record.insert()

        with domain._get_engine().session_manager() as session:
            domains = session.execute(
                "SELECT name FROM domains WHERE name = %s", (domain.name,)
            ).fetchall()
            records = session.execute(
                "SELECT name FROM records WHERE domain_id = %s", (domain.id,)
            ).fetchall()

        assert domains == []
        assert records == []
        domain.delete()

    @pytest.mark.xdist_group(name="pdns")
    def test_txt_record(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        domain1: tp.Dict,
        pdns_server: tp.Optional[int],
    ):
        client = user_api_client(auth_user_admin)

        data = {
            "uuid": str(sys_uuid.uuid4()),
            "project_id": str(c.ZERO_UUID),
            "type": "TXT",
            "ttl": 0,
            "record": {"kind": "TXT", "name": "test", "content": "a" * 5000},
        }

        url = client.build_collection_uri(
            ["dns", "domains", domain1["uuid"], "records"]
        )

        response = client.post(url, json=data)
        output = response.json()

        assert response.status_code == 201
        assert self._cmp_shallow(data, output)

        url = client.build_resource_uri(
            ["dns", "domains", domain1["uuid"], "records", data["uuid"]]
        )

        response = client.get(url)
        record = response.json()
        assert response.status_code == 200
        assert self._cmp_shallow(data, record)

        if pdns_server:
            res = dns.resolver.make_resolver_at("127.0.0.1", port=pdns_server)
            answer = res.resolve(f"test.{DEF_DOMAIN}", "TXT")

            assert len(answer) == 1
            # TXT records may not fit in one UDP frame, so there'll be many
            #  strings inside
            assert "".join([i.decode() for i in answer[0].strings]) == "a" * 5000

        # Delete
        response = client.delete(url)

        assert response.status_code == 204
