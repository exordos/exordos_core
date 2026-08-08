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

from bazooka import exceptions as bazooka_exc
import netaddr
import pytest


def _route(to, via):
    """A route as the model carries one — the types are not strings."""
    return {"to": netaddr.IPNetwork(to), "via": netaddr.IPAddress(via)}


class TestNetworkApi:
    """Functional coverage of the SDN CP API phase 1: Network + Subnet CRUD."""

    def _create_network(self, client, network_factory, **kwargs):
        url = client.build_collection_uri(["network", "networks"])
        network = network_factory(**kwargs)
        response = client.post(url, json=network)
        assert response.status_code == 201, response.text
        return network, response.json()

    def test_create_flat_network(
        self, user_api_client, auth_user_admin, network_factory
    ):
        client = user_api_client(auth_user_admin)
        network, output = self._create_network(
            client, network_factory, driver_spec={"driver": "flat_bridge"}
        )
        assert output["uuid"] == network["uuid"]
        assert output["driver_spec"] == {"driver": "flat_bridge"}
        assert output["access"] == "private"

    def test_create_ovs_evpn_network(
        self, user_api_client, auth_user_admin, network_factory
    ):
        client = user_api_client(auth_user_admin)
        _, output = self._create_network(
            client,
            network_factory,
            name="private-overlay",
            driver_spec={"driver": "ovs_evpn"},
            access="private",
        )
        assert output["driver_spec"]["driver"] == "ovs_evpn"
        assert output["access"] == "private"

    def test_list_networks(self, user_api_client, auth_user_admin, network_factory):
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["network", "networks"])
        for i in range(3):
            self._create_network(client, network_factory, name=f"net_{i}")

        response = client.get(url)
        assert response.status_code == 200
        assert len(response.json()) >= 3

    def test_get_network(self, user_api_client, auth_user_admin, network_factory):
        client = user_api_client(auth_user_admin)
        network, _ = self._create_network(client, network_factory)
        url = client.build_resource_uri(["network", "networks", network["uuid"]])
        response = client.get(url)
        assert response.status_code == 200
        assert response.json()["uuid"] == network["uuid"]

    def test_update_network_access(
        self, user_api_client, auth_user_admin, network_factory
    ):
        client = user_api_client(auth_user_admin)
        network, _ = self._create_network(client, network_factory)
        url = client.build_resource_uri(["network", "networks", network["uuid"]])
        response = client.put(url, json={"name": "renamed-network"})
        assert response.status_code == 200
        assert response.json()["name"] == "renamed-network"

    def test_delete_network(self, user_api_client, auth_user_admin, network_factory):
        client = user_api_client(auth_user_admin)
        network, _ = self._create_network(client, network_factory)
        url = client.build_resource_uri(["network", "networks", network["uuid"]])
        assert client.delete(url).status_code == 204
        with pytest.raises(bazooka_exc.NotFoundError):
            client.get(url)

    def test_publishing_a_network_requires_the_share_permission(
        self, user_api_client, auth_test1_user, network_factory
    ):
        """access=public exposes the network to everyone — it is gated by
        network.network.share (§8). Creating a private one never is. Uses a
        real non-admin user, since admin's *.*.* would pass any gate."""
        creator = user_api_client(
            auth_test1_user,
            permissions=["network.network.create", "network.network.read"],
        )
        url = creator.build_collection_uri(["network", "networks"])

        with pytest.raises(bazooka_exc.ForbiddenError):
            creator.post(url, json=network_factory(access="public"))

        assert (
            creator.post(url, json=network_factory(access="private")).status_code == 201
        )

        sharer = user_api_client(
            auth_test1_user,
            permissions=["network.network.create", "network.network.share"],
        )
        assert (
            sharer.post(url, json=network_factory(access="public")).status_code == 201
        )

    def test_create_subnet(
        self, user_api_client, auth_user_admin, network_factory, subnet_factory
    ):
        client = user_api_client(auth_user_admin)
        network, _ = self._create_network(
            client, network_factory, driver_spec={"driver": "ovs_evpn"}
        )

        url = client.build_collection_uri(["network", "subnets"])
        subnet = subnet_factory(network_uuid=network["uuid"], cidr="10.42.0.0/24")
        response = client.post(url, json=subnet)
        assert response.status_code == 201, response.text
        output = response.json()
        assert output["uuid"] == subnet["uuid"]
        assert output["network"] == network["uuid"]
        assert str(output["cidr"]) == "10.42.0.0/24"

    def test_an_overlay_gateway_has_to_be_an_address_of_its_subnet(
        self, user_api_client, auth_user_admin, network_factory, subnet_factory
    ):
        """The host puts the gateway on a leg with the subnet's own prefix
        length, so a next hop outside the subnet is a leg in a network of
        its own — and the guest's traffic leaves for an address nothing
        holds, with nothing said anywhere."""
        client = user_api_client(auth_user_admin)
        network, _ = self._create_network(
            client, network_factory, driver_spec={"driver": "ovs_evpn"}
        )
        url = client.build_collection_uri(["network", "subnets"])
        subnet = subnet_factory(
            network_uuid=network["uuid"],
            cidr="10.42.0.0/24",
            routers=[_route("0.0.0.0/0", "192.0.2.1")],
        )
        with pytest.raises(bazooka_exc.BadRequestError) as exc:
            client.post(url, json=subnet)
        assert "is not an address of" in exc.value.cause.response.text

    def test_an_overlay_subnet_has_one_default_route(
        self, user_api_client, auth_user_admin, network_factory, subnet_factory
    ):
        """The guest is handed one gateway and its host builds one way out;
        with two defaults they were not obliged to be the same one."""
        client = user_api_client(auth_user_admin)
        network, _ = self._create_network(
            client, network_factory, driver_spec={"driver": "ovs_evpn"}
        )
        url = client.build_collection_uri(["network", "subnets"])
        subnet = subnet_factory(
            network_uuid=network["uuid"],
            cidr="10.42.0.0/24",
            routers=[
                _route("0.0.0.0/0", "10.42.0.1"),
                _route("0.0.0.0/0", "10.42.0.2"),
            ],
        )
        with pytest.raises(bazooka_exc.BadRequestError) as exc:
            client.post(url, json=subnet)
        assert "one default route" in exc.value.cause.response.text

    def test_a_flat_subnet_keeps_the_routes_it_always_accepted(
        self, user_api_client, auth_user_admin, network_factory, subnet_factory
    ):
        """The check is the overlay's. `routers` is shared with the flat
        networks, whose accepted input this does not change."""
        client = user_api_client(auth_user_admin)
        network, _ = self._create_network(client, network_factory)
        url = client.build_collection_uri(["network", "subnets"])
        subnet = subnet_factory(
            network_uuid=network["uuid"],
            cidr="10.43.0.0/24",
            routers=[_route("0.0.0.0/0", "192.0.2.1")],
        )
        assert client.post(url, json=subnet).status_code == 201

    def test_two_subnets_of_one_network_may_not_hand_out_one_address(
        self, user_api_client, auth_user_admin, network_factory, subnet_factory
    ):
        """One network is one segment. Two subnets that can both hand out an
        address will hand out the same one to two machines on the same wire,
        and nothing about that failure says what it is."""
        client = user_api_client(auth_user_admin)
        network, _ = self._create_network(client, network_factory)
        url = client.build_collection_uri(["network", "subnets"])
        assert (
            client.post(
                url,
                json=subnet_factory(network_uuid=network["uuid"], cidr="10.44.0.0/22"),
            ).status_code
            == 201
        )
        with pytest.raises(bazooka_exc.BadRequestError) as exc:
            client.post(
                url,
                json=subnet_factory(network_uuid=network["uuid"], cidr="10.44.3.0/24"),
            )
        assert "one segment" in exc.value.cause.response.text

    def test_a_pool_carved_out_with_ip_range_is_allowed(
        self, user_api_client, auth_user_admin, network_factory, subnet_factory
    ):
        """Which is the point of the check: it says what to do about it.

        Pulling the management subnet's range back off the slice makes the
        two stop overlapping, and that slice is what a pool of floating
        addresses is.
        """
        client = user_api_client(auth_user_admin)
        network, _ = self._create_network(client, network_factory)
        url = client.build_collection_uri(["network", "subnets"])
        management = subnet_factory(
            network_uuid=network["uuid"],
            cidr="10.45.0.0/22",
            ip_range=netaddr.IPRange("10.45.0.1", "10.45.2.254"),
        )
        assert client.post(url, json=management).status_code == 201
        pool = subnet_factory(network_uuid=network["uuid"], cidr="10.45.3.0/24")
        assert client.post(url, json=pool).status_code == 201

    def test_two_realms_may_reuse_one_range_on_networks_of_their_own(
        self, user_api_client, auth_user_admin, network_factory, subnet_factory
    ):
        """The check must not refuse the design it was written beside.

        Every realm's overlay is handed the same addressing on purpose —
        each is its own VRF. Only subnets of one network share a segment.
        """
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["network", "subnets"])
        for name in ("realm-a", "realm-b"):
            network, _ = self._create_network(
                client, network_factory, name=name, driver_spec={"driver": "ovs_evpn"}
            )
            subnet = subnet_factory(network_uuid=network["uuid"], cidr="10.100.0.0/24")
            assert client.post(url, json=subnet).status_code == 201, name

    def test_list_and_delete_subnet(
        self, user_api_client, auth_user_admin, network_factory, subnet_factory
    ):
        client = user_api_client(auth_user_admin)
        network, _ = self._create_network(client, network_factory)

        url = client.build_collection_uri(["network", "subnets"])
        subnet = subnet_factory(network_uuid=network["uuid"])
        assert client.post(url, json=subnet).status_code == 201

        response = client.get(url)
        assert response.status_code == 200
        assert any(s["uuid"] == subnet["uuid"] for s in response.json())

        res_url = client.build_resource_uri(["network", "subnets", subnet["uuid"]])
        assert client.delete(res_url).status_code == 204
