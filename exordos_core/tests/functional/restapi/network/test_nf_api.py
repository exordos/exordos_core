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
from restalchemy.dm import filters as dm_filters

from exordos_core.common import constants as c
from exordos_core.user_api.network.dm import models as network_models


def _seeded(kind, config, **owner):
    """A function as the compiler makes one: for something that owns it."""
    nf = network_models.NetworkFunction(
        uuid=sys_uuid.uuid4(),
        name="seeded-%s" % kind,
        project_id=c.ZERO_UUID,
        kind=kind,
        config=config,
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
        with pytest.raises(bazooka_exc.BaseHTTPException) as exc:
            client.post(self._url(client), json=nf_factory())
        assert exc.value.cause.response.status_code == 405

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

    def test_rejects_an_unknown_config_key(self, user_api_client, auth_user_admin):
        """A typo must fail loudly, not compile into a data plane that
        quietly ignores it."""
        client = user_api_client(auth_user_admin)
        nf = _seeded("dns", {}, owner_network=sys_uuid.uuid4())
        try:
            with pytest.raises(bazooka_exc.BadRequestError):
                client.put(self._res(client, nf), json={"config": {"forwarderz": []}})
        finally:
            nf.collect_generated()

    def test_rejects_a_config_value_of_the_wrong_type(
        self, user_api_client, auth_user_admin
    ):
        client = user_api_client(auth_user_admin)
        nf = _seeded("dns", {}, owner_network=sys_uuid.uuid4())
        try:
            with pytest.raises(bazooka_exc.BadRequestError):
                client.put(
                    self._res(client, nf), json={"config": {"forwarders": "nope"}}
                )
        finally:
            nf.collect_generated()

    @pytest.mark.parametrize(
        "forwarder", ["10.0.0.53", "169.254.169.254", "127.0.0.1", "resolver.lan"]
    )
    def test_a_forwarder_the_hypervisor_would_reach_inward_is_refused(
        self, user_api_client, auth_user_admin, forwarder
    ):
        """The responder relays from the hypervisor, past the fabric guard,
        so a private forwarder is a relay into the underlay."""
        client = user_api_client(auth_user_admin)
        nf = _seeded("dns", {}, owner_network=sys_uuid.uuid4())
        try:
            with pytest.raises(bazooka_exc.BadRequestError) as exc:
                client.put(
                    self._res(client, nf),
                    json={"config": {"forwarders": [forwarder]}},
                )
            assert "forwarder" in exc.value.cause.response.text
        finally:
            nf.collect_generated()

    def test_private_default_forwarders_are_not_stored_as_overrides(
        self, user_api_client, auth_user_admin
    ):
        client = user_api_client(auth_user_admin)
        nf = _seeded("dns", {}, owner_network=sys_uuid.uuid4())
        try:
            response = client.put(
                self._res(client, nf), json={"config": {"zone_suffix": "lab"}}
            )
            assert response.json()["config"] == {"zone_suffix": "lab"}
            stored = network_models.NetworkFunction.objects.get_one(
                filters={"uuid": dm_filters.EQ(nf.uuid)}
            )
            assert stored.render_config({"forwarders": ["10.1.0.2"]}) == {
                "forwarders": ["10.1.0.2"],
                "zone_suffix": "lab",
            }
        finally:
            nf.collect_generated()

    def test_config_replaces_overrides_and_empty_resets_them(
        self, user_api_client, auth_user_admin
    ):
        client = user_api_client(auth_user_admin)
        nf = _seeded(
            "dns",
            {"forwarders": ["1.1.1.1"], "zone_suffix": "internal"},
            owner_network=sys_uuid.uuid4(),
        )
        try:
            url = self._res(client, nf)
            client.put(
                url, json={"config": {"forwarders": ["8.8.8.8"], "zone_suffix": "old"}}
            )
            updated = client.put(url, json={"config": {"zone_suffix": "new"}}).json()
            assert updated["config"] == {"zone_suffix": "new"}
            unchanged = client.put(url, json={"name": "renamed"}).json()
            assert unchanged["config"] == updated["config"]
            reset = client.put(url, json={"config": {}}).json()
            assert reset["config"] == {}
            assert "effective_config" not in reset and "generated_config" not in reset
        finally:
            nf.collect_generated()
