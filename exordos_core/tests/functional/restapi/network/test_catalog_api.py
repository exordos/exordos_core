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
import netaddr
import pytest
from restalchemy.dm import filters as dm_filters

from exordos_core.common import constants as c
from exordos_core.compute.dm import models as compute_models
from exordos_core.user_api.network.dm import models as net_models


def _associate(address_uuid, port):
    """What the compiler writes when a port starts (or stops) answering on
    an address; the API refuses to let a caller say it."""
    addr = net_models.Address.objects.get_one(
        filters={"uuid": dm_filters.EQ(sys_uuid.UUID(str(address_uuid)))}
    )
    addr.association = sys_uuid.UUID(str(port)) if port else None
    from restalchemy.storage.sql import orm

    orm.SQLStorableMixin.update(addr)


def _create_sg(client, url, body):
    declaration = dict(body)
    rules = declaration.pop("rules", [])
    response = client.post(url, json=declaration)
    rule_url = client.build_collection_uri(
        ["network", "catalog", "security_group_rules"]
    )
    for rule in rules:
        client.post(
            rule_url,
            json={
                **rule,
                "security_group": response.json()["uuid"],
                "project_id": declaration["project_id"],
            },
        )
    return response


class TestSecurityGroupApi:
    """Security-group reference object."""

    def _url(self, client):
        return client.build_collection_uri(["network", "catalog", "security_groups"])

    def test_create_security_group(
        self, user_api_client, auth_user_admin, security_group_factory
    ):
        client = user_api_client(auth_user_admin)
        sg = security_group_factory(
            name="web-sg",
            rules=[
                {"direction": "ingress", "protocol": "tcp", "port": 443},
                {"direction": "egress", "protocol": "any"},
            ],
        )
        response = _create_sg(client, self._url(client), sg)
        assert response.status_code == 201, response.text
        output = response.json()
        assert output["uuid"] == sg["uuid"]
        output = client.get(self._url(client) + output["uuid"]).json()
        assert len(output["rules"]) == 2

    def test_create_security_group_rejects_bad_direction(
        self, user_api_client, auth_user_admin, security_group_factory
    ):
        client = user_api_client(auth_user_admin)
        sg = security_group_factory(
            rules=[{"direction": "sideways", "protocol": "tcp", "port": 80}]
        )
        with pytest.raises(bazooka_exc.BadRequestError) as exc:
            _create_sg(client, self._url(client), sg)
        assert "sideways" in str(exc.value.cause.response.text)

    def test_create_security_group_rejects_a_forged_remote_ip(
        self, user_api_client, auth_user_admin, security_group_factory
    ):
        """`remote_ip` reaches the agent as an OpenFlow match, where a comma
        starts another field — it must be an address or a prefix, nothing
        else."""
        client = user_api_client(auth_user_admin)
        sg = security_group_factory(
            rules=[
                {
                    "direction": "egress",
                    "protocol": "tcp",
                    "remote_ip": "10.0.0.0/8,actions=NORMAL",
                }
            ]
        )
        with pytest.raises(bazooka_exc.BadRequestError) as exc:
            _create_sg(client, self._url(client), sg)
        assert "remote_ip" in str(exc.value.cause.response.text)

    def test_list_get_delete_security_group(
        self, user_api_client, auth_user_admin, security_group_factory
    ):
        client = user_api_client(auth_user_admin)
        sg = security_group_factory(name="listable-sg")
        assert _create_sg(client, self._url(client), sg).status_code == 201

        listed = client.get(self._url(client))
        assert listed.status_code == 200
        assert any(item["uuid"] == sg["uuid"] for item in listed.json())

        res_url = client.build_resource_uri(
            ["network", "catalog", "security_groups", sg["uuid"]]
        )
        assert client.get(res_url).status_code == 200
        assert client.delete(res_url).status_code == 204
        with pytest.raises(bazooka_exc.NotFoundError):
            client.get(res_url)


class TestAddressApi:
    """Address reference object / IPAM ledger."""

    def _url(self, client):
        return client.build_collection_uri(["network", "catalog", "addresses"])

    def _subnet(self, client, network_factory, subnet_factory):
        net_url = client.build_collection_uri(["network", "networks"])
        network = network_factory(driver_spec={"driver": "ovs_evpn"})
        assert client.post(net_url, json=network).status_code == 201
        sub_url = client.build_collection_uri(["network", "subnets"])
        subnet = subnet_factory(network_uuid=network["uuid"], cidr="10.42.0.0/24")
        assert client.post(sub_url, json=subnet).status_code == 201
        return subnet

    def test_allocate_address(
        self,
        user_api_client,
        auth_user_admin,
        network_factory,
        subnet_factory,
        address_factory,
    ):
        client = user_api_client(auth_user_admin)
        subnet = self._subnet(client, network_factory, subnet_factory)

        addr = address_factory(subnet_uuid=subnet["uuid"], address="10.42.0.10")
        response = client.post(self._url(client), json=addr)
        assert response.status_code == 201, response.text
        output = response.json()
        assert output["address"] == "10.42.0.10"
        assert output.get("association") is None

    def test_auto_allocate_address(
        self,
        user_api_client,
        auth_user_admin,
        network_factory,
        subnet_factory,
        address_factory,
    ):
        client = user_api_client(auth_user_admin)
        subnet = self._subnet(client, network_factory, subnet_factory)

        # No explicit address -> IPAM allocates the first free host, skipping
        # the network address (10.42.0.0) and the subnet's default gateway
        # (10.42.0.1, reserved on a DHCP subnet), so it starts at .2.
        addr = address_factory(subnet_uuid=subnet["uuid"])
        response = client.post(self._url(client), json=addr)
        assert response.status_code == 201, response.text
        output = response.json()
        assert "origin" not in output
        assert output["address"] == "10.42.0.2"

        # Next auto-allocation must not collide with the first.
        addr2 = address_factory(subnet_uuid=subnet["uuid"])
        output2 = client.post(self._url(client), json=addr2).json()
        assert output2["address"] == "10.42.0.3"

    def test_an_address_is_one_of_its_subnets_or_it_is_not_an_address(
        self,
        user_api_client,
        auth_user_admin,
        network_factory,
        subnet_factory,
        address_factory,
    ):
        """The column is a string, so this is the only thing between the
        ledger and a value that is not an address at all. The driver reads
        the ledger every pass so as not to allocate over a reservation, and
        `in subnet.cidr` raises on such a value — one row would switch the
        reservations off for the whole subnet, silently."""
        client = user_api_client(auth_user_admin)
        subnet = self._subnet(client, network_factory, subnet_factory)

        for bad in ("not-an-address", "10.42.0.256", "10.42.0.0/24"):
            addr = address_factory(subnet_uuid=subnet["uuid"], address=bad)
            with pytest.raises(bazooka_exc.BadRequestError) as exc:
                client.post(self._url(client), json=addr)
            assert "not an IP address" in exc.value.cause.response.text, bad

        # Shorter than any address: refused by the column before the check
        # above is reached. Written by hand, because building a model with it
        # is refused too -- which is the same answer, one layer earlier.
        addr = address_factory(subnet_uuid=subnet["uuid"])
        addr["address"] = "5"
        with pytest.raises(bazooka_exc.BadRequestError):
            client.post(self._url(client), json=addr)

        # An address of some other subnet is the same defect one step later:
        # counted against a pool it is not in.
        outside = address_factory(subnet_uuid=subnet["uuid"], address="10.99.0.5")
        with pytest.raises(bazooka_exc.BadRequestError) as exc:
            client.post(self._url(client), json=outside)
        assert "no accessible subnet" in exc.value.cause.response.text

    def test_an_update_cannot_move_an_address_out_of_its_subnet(
        self,
        user_api_client,
        auth_user_admin,
        network_factory,
        subnet_factory,
        address_factory,
    ):
        client = user_api_client(auth_user_admin)
        subnet = self._subnet(client, network_factory, subnet_factory)

        addr = address_factory(subnet_uuid=subnet["uuid"], address="10.42.0.30")
        created = client.post(self._url(client), json=addr)
        assert created.status_code == 201, created.text

        # Nor within it: an update naming only the address is how a pinned
        # one would get past the explicit-address gate on create.
        for value in ("10.99.0.5", "10.42.0.31"):
            with pytest.raises(bazooka_exc.ConflictError):
                client.put(
                    client.build_resource_uri(
                        ["network", "catalog", "addresses", addr["uuid"]]
                    ),
                    json={"address": value},
                )

    def test_an_address_the_subnet_or_a_port_holds_is_refused(
        self,
        user_api_client,
        auth_user_admin,
        network_factory,
        subnet_factory,
        address_factory,
    ):
        """The gateway and a port's address are not in the ledger, and the
        unique index knows nothing of them."""
        client = user_api_client(auth_user_admin)
        subnet = self._subnet(client, network_factory, subnet_factory)
        port = compute_models.Port(
            subnet=sys_uuid.UUID(subnet["uuid"]),
            project_id=c.ZERO_UUID,
            ipv4=netaddr.IPAddress("10.42.0.50"),
            mac=compute_models.Port.generate_mac(),
        )
        port.insert()

        for held in ("10.42.0.1", "10.42.0.50", "10.42.0.255"):
            addr = address_factory(subnet_uuid=subnet["uuid"], address=held)
            with pytest.raises(bazooka_exc.BadRequestError):
                client.post(self._url(client), json=addr)

    def test_exclusive_allocation(
        self,
        user_api_client,
        auth_user_admin,
        network_factory,
        subnet_factory,
        address_factory,
    ):
        client = user_api_client(auth_user_admin)
        subnet = self._subnet(client, network_factory, subnet_factory)

        addr = address_factory(subnet_uuid=subnet["uuid"], address="10.42.0.20")
        assert client.post(self._url(client), json=addr).status_code == 201

        # Same (subnet, address) again -> UNIQUE(subnet, address) violation.
        dup = address_factory(subnet_uuid=subnet["uuid"], address="10.42.0.20")
        with pytest.raises(bazooka_exc.ConflictError):
            client.post(self._url(client), json=dup)

    def test_associate_and_disassociate_keeps_reserved(
        self,
        user_api_client,
        auth_user_admin,
        network_factory,
        subnet_factory,
        address_factory,
    ):
        client = user_api_client(auth_user_admin)
        subnet = self._subnet(client, network_factory, subnet_factory)

        addr = address_factory(subnet_uuid=subnet["uuid"], address="10.42.0.30")
        assert client.post(self._url(client), json=addr).status_code == 201
        res_url = client.build_resource_uri(
            ["network", "catalog", "addresses", addr["uuid"]]
        )

        # Which port answers on an address is the compiler's to say.
        port_a = "11111111-1111-1111-1111-111111111111"
        with pytest.raises(bazooka_exc.ForbiddenError):
            client.put(res_url, json={"association": port_a})

        _associate(addr["uuid"], port_a)
        _associate(addr["uuid"], None)
        output = client.get(res_url).json()
        assert output.get("association") is None

    def test_freeing_an_address_hands_it_back(
        self,
        user_api_client,
        auth_user_admin,
        network_factory,
        subnet_factory,
        address_factory,
    ):
        """A deleted address leaves the allocator ledger and is reusable."""
        client = user_api_client(auth_user_admin)
        subnet = self._subnet(client, network_factory, subnet_factory)

        addr = address_factory(subnet_uuid=subnet["uuid"], address="10.42.0.40")
        assert client.post(self._url(client), json=addr).status_code == 201
        res_url = client.build_resource_uri(
            ["network", "catalog", "addresses", addr["uuid"]]
        )
        assert client.delete(res_url).status_code == 204

        # The address is the subnet's again, and somebody else may take it.
        again = address_factory(subnet_uuid=subnet["uuid"], address="10.42.0.40")
        assert client.post(self._url(client), json=again).status_code == 201

        # And the row that gave it back cannot quietly take it back.
        with pytest.raises(bazooka_exc.NotFoundError):
            client.get(res_url)

    def test_an_address_in_use_is_not_released(
        self,
        user_api_client,
        auth_user_admin,
        network_factory,
        subnet_factory,
        address_factory,
    ):
        """Freeing is releasing, so it is refused for the same reason a
        delete is: something is answering on the address."""
        client = user_api_client(auth_user_admin)
        subnet = self._subnet(client, network_factory, subnet_factory)

        addr = address_factory(subnet_uuid=subnet["uuid"], address="10.42.0.41")
        assert client.post(self._url(client), json=addr).status_code == 201
        res_url = client.build_resource_uri(
            ["network", "catalog", "addresses", addr["uuid"]]
        )
        _associate(addr["uuid"], "11111111-1111-1111-1111-111111111111")

        with pytest.raises(bazooka_exc.ConflictError):
            client.delete(res_url)

        # Disassociated, it releases like any other.
        _associate(addr["uuid"], None)
        assert client.delete(res_url).status_code == 204


class TestCatalogReferentialIntegrity:
    """Reference objects have their own lifecycle, so removing one out from
    under its user must be refused rather than silently change behaviour."""

    def _sg_url(self, client):
        return client.build_collection_uri(["network", "catalog", "security_groups"])

    def _address_url(self, client):
        return client.build_collection_uri(["network", "catalog", "addresses"])

    def test_referenced_security_group_cannot_be_deleted(
        self,
        user_api_client,
        auth_user_admin,
        security_group_factory,
        network_factory,
        subnet_factory,
        port_factory,
    ):
        client = user_api_client(auth_user_admin)
        sg = security_group_factory(
            name="referenced",
            rules=[{"direction": "egress", "protocol": "tcp", "port": 443}],
        )
        _create_sg(client, self._sg_url(client), sg).raise_for_status()

        network = network_factory(name="sg-net", driver_spec={"driver": "ovs_evpn"})
        client.post(
            client.build_collection_uri(["network", "networks"]), json=network
        ).raise_for_status()
        subnet = subnet_factory(network_uuid=network["uuid"], cidr="10.43.0.0/24")
        client.post(
            client.build_collection_uri(["network", "subnets"]), json=subnet
        ).raise_for_status()
        port = port_factory(
            subnet_uuid=subnet["uuid"],
        )
        client.post(
            client.build_collection_uri(["network", "ports"]), json=port
        ).raise_for_status()

        # Dropping the group here would compile the port to an empty
        # allow-list, which the agent applies as "no filtering at all".
        with pytest.raises(bazooka_exc.ConflictError):
            client.delete(self._sg_url(client) + sg["uuid"])

    def test_associated_address_cannot_be_released(
        self,
        user_api_client,
        auth_user_admin,
        address_factory,
        network_factory,
        subnet_factory,
    ):
        client = user_api_client(auth_user_admin)
        network = network_factory(name="addr-net", driver_spec={"driver": "ovs_evpn"})
        client.post(
            client.build_collection_uri(["network", "networks"]), json=network
        ).raise_for_status()
        subnet = subnet_factory(network_uuid=network["uuid"], cidr="10.44.0.0/24")
        client.post(
            client.build_collection_uri(["network", "subnets"]), json=subnet
        ).raise_for_status()

        address = address_factory(subnet_uuid=subnet["uuid"], address="10.44.0.9")
        client.post(self._address_url(client), json=address).raise_for_status()
        url = self._address_url(client) + address["uuid"]
        _associate(address["uuid"], address["uuid"])

        with pytest.raises(bazooka_exc.ConflictError):
            client.delete(url)

        # disassociating releases the hold without freeing the reservation
        _associate(address["uuid"], None)
        client.delete(url).raise_for_status()


class TestPlatformFunctions:
    """The `proxy` function is the installation's, not a caller's.

    It says what the hypervisor fetches from the management network on a
    guest's behalf; a tenant able to edit it turns the host into a relay.
    """

    def _url(self, client):
        return client.build_collection_uri(["network", "nfs"])

    def _seeded_proxy(self, project_id):
        from exordos_core.user_api.network.dm import models as network_models

        nf = network_models.NetworkFunction(
            uuid=sys_uuid.uuid4(),
            name="proxy-seeded",
            project_id=project_id,
            kind="proxy",
            config={"forwards": ["/=http://10.30.0.2:11013"], "ports": {}},
            owner_network=sys_uuid.uuid4(),
        )
        nf.insert()
        return nf

    def test_a_caller_cannot_create_one(self, user_api_client, auth_user_admin):
        """No function is created through the API, and the reason this one
        would be the worst to allow is that the compiler takes the function
        of a network by lowest uuid: a created `proxy` could displace the
        seeded one and become what the hypervisor relays to."""
        client = user_api_client(auth_user_admin)
        with pytest.raises(bazooka_exc.BaseHTTPException) as exc:
            client.post(
                self._url(client),
                json={
                    "uuid": str(sys_uuid.uuid4()),
                    "name": "my-proxy",
                    "kind": "proxy",
                    "config": {"forwards": ["/x=http://10.20.0.2:11010/"]},
                },
            )
        assert exc.value.cause.response.status_code == 405

    def test_a_caller_cannot_point_the_seeded_one_elsewhere(
        self, user_api_client, auth_user_admin
    ):
        client = user_api_client(auth_user_admin)
        nf = self._seeded_proxy(c.ZERO_UUID)
        try:
            with pytest.raises(bazooka_exc.BadRequestError) as exc:
                client.put(
                    client.build_resource_uri(["network", "nfs", str(nf.uuid)]),
                    json={"config": {"forwards": ["/x=http://10.20.0.2:11010/"]}},
                )
            assert "installation" in exc.value.cause.response.text
            # ... and deleting it is editing it by another route: the
            # compiler would seed a fresh one, and the only lasting effect
            # is the window in between, with the network's guests reaching
            # nothing at all.
            with pytest.raises(bazooka_exc.BadRequestError):
                client.delete(
                    client.build_resource_uri(["network", "nfs", str(nf.uuid)])
                )
        finally:
            nf.collect_generated()

    def test_a_resolver_is_still_the_callers(self, user_api_client, auth_user_admin):
        """Only the platform's own kinds are closed: `dns` and `dhcp` exist
        to be edited, which is the whole reason they are objects."""
        from exordos_core.user_api.network.dm import models as network_models

        client = user_api_client(auth_user_admin)
        nf = network_models.NetworkFunction(
            uuid=sys_uuid.uuid4(),
            name="dns-seeded",
            project_id=c.ZERO_UUID,
            kind="dns",
            config={"forwarders": ["1.1.1.1"]},
            owner_network=sys_uuid.uuid4(),
        )
        nf.insert()
        try:
            response = client.put(
                client.build_resource_uri(["network", "nfs", str(nf.uuid)]),
                json={"config": {"forwarders": ["9.9.9.9"]}},
            )
            assert response.status_code == 200, response.text
            assert "provenance" not in response.json()
            for internal in (
                {"records": [{"name": "forged", "ipv4": "10.42.0.9"}]},
                {"_subnet_uuid": str(sys_uuid.uuid4())},
                {"_resource_configs": {}},
            ):
                with pytest.raises(bazooka_exc.BadRequestError):
                    client.put(
                        client.build_resource_uri(["network", "nfs", str(nf.uuid)]),
                        json={"config": internal},
                    )
        finally:
            nf.collect_generated()
