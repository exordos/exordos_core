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

import uuid as sys_uuid

from bazooka import exceptions as bazooka_exc
import pytest

from exordos_core.common import constants as c
from exordos_core.user_api.network.dm import models as network_models


def _seeded(kind, config, **owner):
    """A function as the compiler makes one: for something that owns it."""
    nf = network_models.NetworkFunction(
        uuid=sys_uuid.uuid4(),
        name="seeded-%s" % kind,
        project_id=c.SERVICE_PROJECT_ID,
        kind=kind,
        config=config,
        provenance="generated",
        **owner,
    )
    nf.insert()
    return nf


class TestNetworkFunctionApi:
    """The functions a subnet, a network and a port are served by.

    They are read and edited, never made: one created on its own would
    belong to nothing and be read by nothing, while looking exactly like a
    function that is in force.
    """

    def _url(self, client):
        return client.build_collection_uri(["network", "nfs"])

    def _res(self, client, nf):
        return client.build_resource_uri(["network", "nfs", str(nf.uuid)])

    def test_a_function_is_not_created_on_its_own(
        self, user_api_client, auth_user_admin, nf_factory
    ):
        client = user_api_client(auth_user_admin)
        with pytest.raises(bazooka_exc.BadRequestError) as exc:
            client.post(self._url(client), json=nf_factory(kind="splitter"))
        assert "not created on their own" in exc.value.cause.response.text

    def test_a_seeded_function_is_listed_and_read(
        self, user_api_client, auth_user_admin
    ):
        client = user_api_client(auth_user_admin)
        nf = _seeded("dns", {"forwarders": ["1.1.1.1"]}, owner_network=sys_uuid.uuid4())
        try:
            listed = client.get(self._url(client))
            assert listed.status_code == 200
            assert any(item["uuid"] == str(nf.uuid) for item in listed.json())
            assert client.get(self._res(client, nf)).status_code == 200
        finally:
            nf.collect_generated()

    def test_a_subnets_default_group_is_edited_in_place(
        self, user_api_client, auth_user_admin
    ):
        """The default group is a `splitter` the subnet owns, and editing it
        is how an installation changes what a guest arrives with."""
        client = user_api_client(auth_user_admin)
        nf = _seeded("splitter", {"rules": []}, owner_subnet=sys_uuid.uuid4())
        try:
            response = client.put(
                self._res(client, nf),
                json={
                    "config": {
                        "rules": [
                            {"direction": "ingress", "protocol": "tcp", "port": 443}
                        ]
                    }
                },
            )
            assert response.status_code == 200, response.text
            output = response.json()
            assert output["config"]["rules"][0]["port"] == 443
            # Edited by a caller, so it is the caller's from now on.
            assert output["provenance"] == "user"
        finally:
            nf.collect_generated()

    def test_a_ports_own_expansion_is_read_only(self, user_api_client, auth_user_admin):
        """What a port compiles into restates the port: edited here it would
        be edited away on the next pass."""
        client = user_api_client(auth_user_admin)
        nf = _seeded("splitter", {"rules": []}, owner_port=sys_uuid.uuid4())
        try:
            with pytest.raises(bazooka_exc.BadRequestError) as exc:
                client.put(self._res(client, nf), json={"config": {"rules": []}})
            assert "read-only" in exc.value.cause.response.text
        finally:
            nf.collect_generated()

    def test_rejects_an_unknown_config_key(self, user_api_client, auth_user_admin):
        """A typo must fail loudly, not compile into a data plane that
        quietly ignores it."""
        client = user_api_client(auth_user_admin)
        nf = _seeded("splitter", {"rules": []}, owner_subnet=sys_uuid.uuid4())
        try:
            with pytest.raises(bazooka_exc.BadRequestError):
                client.put(self._res(client, nf), json={"config": {"rulez": []}})
        finally:
            nf.collect_generated()

    def test_rejects_a_config_value_of_the_wrong_type(
        self, user_api_client, auth_user_admin
    ):
        client = user_api_client(auth_user_admin)
        nf = _seeded("splitter", {"rules": []}, owner_subnet=sys_uuid.uuid4())
        try:
            with pytest.raises(bazooka_exc.BadRequestError):
                client.put(self._res(client, nf), json={"config": {"rules": "nope"}})
        finally:
            nf.collect_generated()

    def test_splitter_rules_are_validated_like_a_security_group(
        self, user_api_client, auth_user_admin
    ):
        """A splitter reaches the same OpenFlow matches as the friendly
        surface, so it cannot be the lax way in."""
        client = user_api_client(auth_user_admin)
        nf = _seeded("splitter", {"rules": []}, owner_subnet=sys_uuid.uuid4())
        try:
            with pytest.raises(bazooka_exc.BadRequestError) as exc:
                client.put(
                    self._res(client, nf),
                    json={
                        "config": {
                            "rules": [
                                {
                                    "direction": "egress",
                                    "protocol": "tcp",
                                    "remote_ip": "10.0.0.0/8,actions=NORMAL",
                                }
                            ]
                        }
                    },
                )
            assert "remote_ip" in str(exc.value.cause.response.text)
        finally:
            nf.collect_generated()
