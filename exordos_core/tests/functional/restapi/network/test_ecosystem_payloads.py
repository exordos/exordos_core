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

"""Validate that the exact payloads the exordos_ecosystem realm manager sends
(border, DNS records, wildcard cert) are accepted by the live core API."""

import uuid as sys_uuid

from exordos_core.common import constants as c

PROJECT = str(c.SERVICE_PROJECT_ID)


class TestEcosystemPayloads:
    def test_border_inline_rules(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["network", "border"])
        body = {
            "uuid": str(sys_uuid.uuid4()),
            "name": "realm-border",
            "project_id": PROJECT,
            "node": str(sys_uuid.uuid4()),
            "forwards": [
                {
                    "proto": "tcp",
                    "public_ip": None,
                    "listen_port": 11010,
                    "to_host": "192.168.100.2",
                    "to_port": 11010,
                },
                {
                    "proto": "udp",
                    "public_ip": None,
                    "listen_port": 53,
                    "to_host": "192.168.100.2",
                    "to_port": 5300,
                },
            ],
            "snat_rules": [
                {
                    "source_cidr": "192.168.100.0/24",
                    "mode": "masquerade",
                    "snat_to": None,
                }
            ],
        }
        resp = client.post(url, json=body)
        assert resp.status_code == 201, resp.text
        got = resp.json()
        assert got["node"] == body["node"]
        assert got["forwards"][0]["listen_port"] == 11010
        assert got["snat_rules"][0]["source_cidr"] == "192.168.100.0/24"

    def test_dns_domain_and_wildcard_a_record(self, user_api_client, auth_user_admin):
        client = user_api_client(auth_user_admin)
        duuid = str(sys_uuid.uuid4())
        dresp = client.post(
            client.build_collection_uri(["dns", "domains"]),
            json={"uuid": duuid, "name": "exordos.io", "project_id": PROJECT},
        )
        assert dresp.status_code == 201, dresp.text

        rresp = client.post(
            client.build_collection_uri(["dns", "domains", duuid, "records"]),
            json={
                "uuid": str(sys_uuid.uuid4()),
                "type": "A",
                "ttl": 300,
                "record": {
                    "kind": "A",
                    "name": "*.c0ffee.exordos.io",
                    "address": "203.0.113.9",
                },
            },
        )
        assert rresp.status_code == 201, rresp.text
