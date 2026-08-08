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
from exordos_core.compute.dm import models as compute_models


class TestSecurityGroupApi:
    """SDN CP API phase 2: security-group reference object (§6.2)."""

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
        response = client.post(self._url(client), json=sg)
        assert response.status_code == 201, response.text
        output = response.json()
        assert output["uuid"] == sg["uuid"]
        assert len(output["rules"]) == 2

    def test_create_security_group_rejects_bad_direction(
        self, user_api_client, auth_user_admin, security_group_factory
    ):
        client = user_api_client(auth_user_admin)
        sg = security_group_factory(
            rules=[{"direction": "sideways", "protocol": "tcp", "port": 80}]
        )
        with pytest.raises(bazooka_exc.BadRequestError) as exc:
            client.post(self._url(client), json=sg)
        assert "direction" in str(exc.value.cause.response.text)

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
            client.post(self._url(client), json=sg)
        assert "remote_ip" in str(exc.value.cause.response.text)

    def test_list_get_delete_security_group(
        self, user_api_client, auth_user_admin, security_group_factory
    ):
        client = user_api_client(auth_user_admin)
        sg = security_group_factory(name="listable-sg")
        assert client.post(self._url(client), json=sg).status_code == 201

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
    """SDN CP API phase 2: address reference object / IPAM ledger (§6.1)."""

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
        assert output["allocation"] == "reserved"
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
        assert output["origin"] == "auto"
        assert output["address"] == "10.42.0.2"

        # Next auto-allocation must not collide with the first.
        addr2 = address_factory(subnet_uuid=subnet["uuid"])
        output2 = client.post(self._url(client), json=addr2).json()
        assert output2["address"] == "10.42.0.3"

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

        # Associate to some port uuid (move = point at a different port).
        port_a = "11111111-1111-1111-1111-111111111111"
        response = client.put(res_url, json={"association": port_a})
        assert response.status_code == 200
        assert response.json()["association"] == port_a

        # Disassociate -> association cleared but the address stays reserved.
        response = client.put(res_url, json={"association": None})
        assert response.status_code == 200
        output = response.json()
        assert output.get("association") is None
        assert output["allocation"] == "reserved"

    def test_freeing_an_address_hands_it_back(
        self,
        user_api_client,
        auth_user_admin,
        network_factory,
        subnet_factory,
        address_factory,
    ):
        """`freed` used to mean nothing at all: the row stayed in the set the
        allocator scans, so releasing an address made it unavailable for
        ever."""
        client = user_api_client(auth_user_admin)
        subnet = self._subnet(client, network_factory, subnet_factory)

        addr = address_factory(subnet_uuid=subnet["uuid"], address="10.42.0.40")
        assert client.post(self._url(client), json=addr).status_code == 201
        res_url = client.build_resource_uri(
            ["network", "catalog", "addresses", addr["uuid"]]
        )
        assert client.put(res_url, json={"allocation": "freed"}).status_code == 200

        # The address is the subnet's again, and somebody else may take it.
        again = address_factory(subnet_uuid=subnet["uuid"], address="10.42.0.40")
        assert client.post(self._url(client), json=again).status_code == 201

        # And the row that gave it back cannot quietly take it back.
        with pytest.raises(bazooka_exc.BadRequestError) as exc:
            client.put(res_url, json={"allocation": "reserved"})
        assert "somebody else" in str(exc.value.cause.response.text)

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
        port = "11111111-1111-1111-1111-111111111111"
        assert client.put(res_url, json={"association": port}).status_code == 200

        with pytest.raises(bazooka_exc.BadRequestError) as exc:
            client.put(res_url, json={"allocation": "freed"})
        assert "still associated" in str(exc.value.cause.response.text)

        # Disassociated, it releases like any other.
        assert client.put(res_url, json={"association": None}).status_code == 200
        assert client.put(res_url, json={"allocation": "freed"}).status_code == 200


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
        client.post(self._sg_url(client), json=sg).raise_for_status()

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
            config=compute_models.PortSimpleKind(
                security_groups=[sys_uuid.UUID(sg["uuid"])]
            ),
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
        client.put(url, json={"association": address["uuid"]}).raise_for_status()

        with pytest.raises(bazooka_exc.ConflictError):
            client.delete(url)

        # disassociating releases the hold without freeing the reservation
        client.put(url, json={"association": None}).raise_for_status()
        client.delete(url).raise_for_status()


class TestIdentityGroupBoundaries:
    """What a group is worth depends on which network's bits it names."""

    def _url(self, client):
        return client.build_collection_uri(["network", "catalog", "identity_groups"])

    def _sg_url(self, client):
        return client.build_collection_uri(["network", "catalog", "security_groups"])

    def _network(self, client, network_factory, name="net-identity"):
        network = network_factory(name=name, driver_spec={"driver": "ovs_evpn"})
        client.post(client.build_collection_uri(["network", "networks"]), json=network)
        return network

    def test_a_rule_may_not_name_another_projects_group(
        self,
        user_api_client,
        auth_user_admin,
        network_factory,
        security_group_factory,
    ):
        """Every other reference is held to the project boundary; this one
        was checked for existence only.

        Identity bits are allocated **per network**, so a rule naming a group
        on a network the caller does not own compiles to a bit that, on the
        port's own network, belongs to an entirely different group. The
        caller is granted something other than what they asked for — which
        is the part that matters, whatever the packets do.
        """
        client = user_api_client(auth_user_admin)
        network = self._network(client, network_factory)
        foreign = {
            "uuid": str(sys_uuid.uuid4()),
            "name": "foreign-group",
            "project_id": str(sys_uuid.uuid4()),
            "network": network["uuid"],
            "identity": 4,
        }
        from exordos_core.user_api.network.dm import models as network_models

        # The group and the network it spends a bit of go together: a
        # network's sixteen bits are its owner's, so a group of another
        # project is one on another project's network.
        foreign_network = compute_models.Network(
            uuid=sys_uuid.uuid4(),
            name="foreign-network",
            project_id=sys_uuid.UUID(foreign["project_id"]),
            driver_spec={"driver": "ovs_evpn"},
        )
        foreign_network.insert()
        foreign["network"] = str(foreign_network.uuid)
        network_models.IdentityGroup(
            uuid=sys_uuid.UUID(foreign["uuid"]),
            name=foreign["name"],
            project_id=sys_uuid.UUID(foreign["project_id"]),
            network=sys_uuid.UUID(foreign["network"]),
            identity=foreign["identity"],
        ).insert()

        sg = security_group_factory(
            name="names-a-foreign-group",
            rules=[
                {
                    "direction": "ingress",
                    "protocol": "any",
                    "remote_group": foreign["uuid"],
                }
            ],
        )
        with pytest.raises(bazooka_exc.BadRequestError) as exc:
            client.post(self._sg_url(client), json=sg)
        assert "another project" in exc.value.cause.response.text


class TestSubnetIdentityCapacity:
    """A subnet's default group takes one of its network's sixteen bits."""

    def test_a_network_out_of_bits_still_gets_its_next_subnet(
        self,
        user_api_client,
        auth_user_admin,
        network_factory,
        subnet_factory,
    ):
        """The ceiling is on what rides in the packet, not on how many
        groups a network may have.

        This used to be refused, because a subnet whose guests could not be
        given a default group at all was not a state worth reaching. It no
        longer is one: past the bits a group is carried as an address set,
        so the subnet gets its default either way.
        """
        from exordos_core.user_api.network.dm import models as network_models

        client = user_api_client(auth_user_admin)
        network = network_factory(name="net-full", driver_spec={"driver": "ovs_evpn"})
        client.post(client.build_collection_uri(["network", "networks"]), json=network)
        for bit in range(network_models.IdentityGroup.IDENTITY_BITS):
            network_models.IdentityGroup(
                uuid=sys_uuid.uuid4(),
                name="filler-%d" % bit,
                project_id=sys_uuid.UUID(network["project_id"]),
                network=sys_uuid.UUID(network["uuid"]),
                identity=1 << bit,
            ).insert()

        subnet = subnet_factory(network_uuid=network["uuid"], name="one-too-many")
        client.post(client.build_collection_uri(["network", "subnets"]), json=subnet)

    def test_a_group_past_the_bits_is_carried_by_its_members(
        self,
        user_api_client,
        auth_user_admin,
        network_factory,
    ):
        """Allocation decides which of the two mechanisms answers for a
        group, and it decides once — a group that changed hands between them
        would be a rule compiled against a number nobody stamps."""
        from exordos_core.user_api.network.dm import models as network_models

        client = user_api_client(auth_user_admin)
        network = network_factory(name="net-sets", driver_spec={"driver": "ovs_evpn"})
        client.post(client.build_collection_uri(["network", "networks"]), json=network)
        uri = client.build_collection_uri(["network", "catalog", "identity_groups"])
        for index in range(network_models.IdentityGroup.IDENTITY_BITS + 2):
            client.post(
                uri,
                json={
                    "uuid": str(sys_uuid.uuid4()),
                    "name": "group-%d" % index,
                    "project_id": network["project_id"],
                    "network": network["uuid"],
                },
            )

        groups = network_models.IdentityGroup.objects.get_all(
            filters={"network": dm_filters.EQ(sys_uuid.UUID(network["uuid"]))}
        )
        bits = [g for g in groups if g.identity is not None]
        sets = [g for g in groups if g.conj_id is not None]
        assert len(bits) == network_models.IdentityGroup.IDENTITY_BITS
        assert len(sets) == 2, "the rest are carried as address sets"
        assert all(g.identity is None for g in sets), "never both at once"
        assert len({g.conj_id for g in sets}) == 2, "and each has its own number"


class TestPlatformFunctions:
    """The `proxy` function is the installation's, not a caller's.

    Its `forwards` and `ports` say which upstreams the *hypervisor* fetches
    on a guest's behalf, from the management network the overlay exists to
    keep guests out of. It is seeded in the tenant's own project, so without
    these gates it would be theirs to edit like any object of theirs — and a
    tenant that edits it turns the host into a relay into the underlay.
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
            provenance="generated",
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
        with pytest.raises(bazooka_exc.BadRequestError) as exc:
            client.post(
                self._url(client),
                json={
                    "uuid": str(sys_uuid.uuid4()),
                    "name": "my-proxy",
                    "kind": "proxy",
                    "config": {"forwards": ["/x=http://10.20.0.2:11010/"]},
                },
            )
        assert "not created on their own" in exc.value.cause.response.text

    def test_a_caller_cannot_point_the_seeded_one_elsewhere(
        self, user_api_client, auth_user_admin
    ):
        client = user_api_client(auth_user_admin)
        nf = self._seeded_proxy(c.SERVICE_PROJECT_ID)
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
            project_id=c.SERVICE_PROJECT_ID,
            kind="dns",
            config={"forwarders": ["1.1.1.1"]},
            provenance="generated",
            owner_network=sys_uuid.uuid4(),
        )
        nf.insert()
        try:
            response = client.put(
                client.build_resource_uri(["network", "nfs", str(nf.uuid)]),
                json={"config": {"forwarders": ["9.9.9.9"]}},
            )
            assert response.status_code == 200, response.text
            assert response.json()["provenance"] == "user"
        finally:
            nf.collect_generated()


class TestIdentityIsNotClaimable:
    """A bit is a membership, and a membership is not a thing to assert.

    Identity bits are allocated per network and travel in the packet, so
    every way of acquiring one is a way of being admitted by rules that name
    the group holding it. Three of them were open.
    """

    def _networks_url(self, client):
        return client.build_collection_uri(["network", "networks"])

    def _groups_url(self, client):
        return client.build_collection_uri(["network", "catalog", "identity_groups"])

    def _network(self, client, network_factory, name, project_id=None):
        network = network_factory(name=name, driver_spec={"driver": "ovs_evpn"})
        if project_id is not None:
            network["project_id"] = str(project_id)
        client.post(self._networks_url(client), json=network)
        return network

    def test_a_group_may_not_be_created_on_a_foreign_network(
        self, user_api_client, auth_user_admin, network_factory
    ):
        """Sixteen bits belong to whoever owns the network, and spending them
        is a denial of service with a long reach: the owner's next subnet is
        refused, and the subnets they already have compile to deny-all once a
        default group cannot be seeded for them."""
        from exordos_core.compute.dm import models as compute_models

        client = user_api_client(auth_user_admin)
        foreign_project = sys_uuid.uuid4()
        foreign = compute_models.Network(
            uuid=sys_uuid.uuid4(),
            name="someone-elses",
            project_id=foreign_project,
            driver_spec={"driver": "ovs_evpn"},
        )
        foreign.insert()
        try:
            with pytest.raises(bazooka_exc.BadRequestError) as exc:
                client.post(
                    self._groups_url(client),
                    json={
                        "uuid": str(sys_uuid.uuid4()),
                        "name": "squatter",
                        "project_id": str(c.SERVICE_PROJECT_ID),
                        "network": str(foreign.uuid),
                    },
                )
            assert "another project" in exc.value.cause.response.text
        finally:
            foreign.delete()

    def test_a_rule_may_not_name_a_bit_directly(
        self, user_api_client, auth_user_admin, security_group_factory
    ):
        """`remote_identity` is what a group travels as on the wire. Asked
        for directly it is a membership claim with no group whose project can
        be checked — the way round the reference check itself."""
        client = user_api_client(auth_user_admin)
        sg = security_group_factory(
            name="names-a-bit",
            rules=[{"direction": "ingress", "protocol": "any", "remote_identity": 1}],
        )
        with pytest.raises(bazooka_exc.BadRequestError) as exc:
            client.post(
                client.build_collection_uri(["network", "catalog", "security_groups"]),
                json=sg,
            )
        assert "remote_group" in exc.value.cause.response.text

    def test_a_caller_cannot_claim_to_be_the_generator(
        self, user_api_client, auth_user_admin
    ):
        """The wire form of a group is admissible in what the compiler
        published, so `provenance` decides whether a ruleset may carry it.
        A caller editing a seeded function takes it over — and takes it over
        as theirs, whatever they say about who wrote it."""
        from exordos_core.user_api.network.dm import models as network_models

        client = user_api_client(auth_user_admin)
        nf = network_models.NetworkFunction(
            uuid=sys_uuid.uuid4(),
            name="subnet-default",
            project_id=c.SERVICE_PROJECT_ID,
            kind="splitter",
            config={"rules": []},
            provenance="generated",
            owner_subnet=sys_uuid.uuid4(),
        )
        nf.insert()
        try:
            response = client.put(
                client.build_resource_uri(["network", "nfs", str(nf.uuid)]),
                json={"provenance": "generated", "config": {"rules": []}},
            )
            assert response.status_code == 200, response.text
            assert response.json()["provenance"] == "user"
        finally:
            nf.collect_generated()
