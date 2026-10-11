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


class TestPortApi:
    """Canonical ports and independent group memberships."""

    def _subnet(self, client, network_factory, subnet_factory):
        net_url = client.build_collection_uri(["network", "networks"])
        network = network_factory(driver_spec={"driver": "ovs_evpn"})
        assert client.post(net_url, json=network).status_code == 201
        sub_url = client.build_collection_uri(["network", "subnets"])
        subnet = subnet_factory(network_uuid=network["uuid"], cidr="10.42.0.0/24")
        assert client.post(sub_url, json=subnet).status_code == 201
        return subnet

    def _url(self, client):
        return client.build_collection_uri(["network", "ports"])

    def _security_group(self, client, security_group_factory):
        url = client.build_collection_uri(["network", "catalog", "security_groups"])
        sg = security_group_factory(
            name="port-sg", rules=[{"direction": "ingress", "protocol": "icmp"}]
        )
        from exordos_core.tests.functional.restapi.network.test_catalog_api import (
            _create_sg,
        )

        assert _create_sg(client, url, sg).status_code == 201
        return sg

    def test_create_port_defaults_to_simple(
        self,
        user_api_client,
        auth_user_admin,
        network_factory,
        subnet_factory,
        port_factory,
    ):
        client = user_api_client(auth_user_admin)
        subnet = self._subnet(client, network_factory, subnet_factory)

        port = port_factory(subnet_uuid=subnet["uuid"])
        response = client.post(self._url(client), json=port)
        assert response.status_code == 201, response.text
        output = response.json()
        assert output["config"]["kind"] == "simple"
        assert output["port_security"] is True
        assert output["subnet"] == subnet["uuid"]

    def test_a_port_cannot_borrow_another_projects_group(
        self,
        user_api_client,
        auth_user_admin,
        network_factory,
        subnet_factory,
        port_factory,
    ):
        """Every SDN object cites the others by bare uuid. Unchecked, a
        caller could attach somebody else's group — and the owner could
        then never delete it, because referential integrity scans every
        project and would answer 409 forever."""
        from exordos_core.user_api.network.dm import models as net_models

        client = user_api_client(auth_user_admin)
        subnet = self._subnet(client, network_factory, subnet_factory)

        foreign = net_models.SecurityGroup(
            uuid=sys_uuid.uuid4(),
            name="someone-elses-sg",
            project_id=sys_uuid.uuid4(),
            rules=[{"direction": "ingress", "protocol": "tcp", "port": 443}],
        )
        foreign.insert()

        port = port_factory(
            subnet_uuid=subnet["uuid"],
        )
        with pytest.raises(bazooka_exc.BadRequestError) as exc:
            client.post(
                client.build_collection_uri(["network", "port_security_groups"]),
                json={
                    "project_id": str(c.ZERO_UUID),
                    "port": client.post(self._url(client), json=port).json()["uuid"],
                    "security_group": str(foreign.uuid),
                },
            )
        assert "another project" in str(exc.value.cause.response.text)

        # ... and the refusal leaves the owner's object deletable
        foreign.delete()

    def test_a_port_cannot_name_another_projects_node(
        self,
        user_api_client,
        auth_user_admin,
        network_factory,
        subnet_factory,
        port_factory,
    ):
        """A port is a node's leg and carries its project, which is what the
        compiler does when it builds one. Named the other way round, the row
        would have the reconcile loop write a stranger's `default_network`,
        schedule a data plane slice to the hypervisor serving them, and offer
        itself as the interface their machine comes up with."""
        from gcl_sdk.infra.dm import models as sdk_models

        client = user_api_client(auth_user_admin)
        subnet = self._subnet(client, network_factory, subnet_factory)

        victim = compute_models.Node(
            uuid=sys_uuid.uuid4(),
            name="someone-elses-node",
            cores=1,
            ram=1024,
            disk_spec=sdk_models.RootDiskSpec(image="ubuntu_24.04"),
            project_id=sys_uuid.uuid4(),
        )
        victim.insert()
        try:
            port = port_factory(subnet_uuid=subnet["uuid"], node=victim.uuid)
            with pytest.raises(bazooka_exc.BadRequestError) as exc:
                client.post(self._url(client), json=port)
            assert "another project" in exc.value.cause.response.text
        finally:
            victim.delete()

    def test_a_port_given_to_a_node_outlives_it(
        self, user_api_client, auth_user_admin, network_factory, subnet_factory
    ):
        """Only the legs the compiler made for a node go with it; a port the
        node was given is the caller's and is let go of."""
        from gcl_sdk.infra.dm import models as sdk_models

        client = user_api_client(auth_user_admin)
        subnet = self._subnet(client, network_factory, subnet_factory)
        node = compute_models.Node(
            uuid=sys_uuid.uuid4(),
            name="leg-owner",
            cores=1,
            ram=1024,
            disk_spec=sdk_models.RootDiskSpec(image="ubuntu_24.04"),
            project_id=c.ZERO_UUID,
        )
        node.insert()
        ports = {}
        for index, provenance in enumerate(("user", "generated")):
            port = compute_models.Port(
                uuid=sys_uuid.uuid4(),
                subnet=sys_uuid.UUID(subnet["uuid"]),
                node=node.uuid,
                index=index,
                project_id=c.ZERO_UUID,
                provenance=provenance,
            )
            port.insert()
            ports[provenance] = port.uuid

        node.delete()

        def _stored(uuid):
            return compute_models.Port.objects.get_one_or_none(
                filters={"uuid": dm_filters.EQ(uuid)}
            )

        given = _stored(ports["user"])
        assert given is not None and given.node is None
        generated = _stored(ports["generated"])
        assert generated is not None and generated.node is None

    def test_a_caller_cannot_claim_the_compilers_provenance(
        self,
        user_api_client,
        auth_user_admin,
        network_factory,
        subnet_factory,
        port_factory,
    ):
        """`generated` is what lets the installation's own port for a
        bare-metal node sit in a project other than its node's. A caller who
        could write it would have the exemption too."""
        client = user_api_client(auth_user_admin)
        subnet = self._subnet(client, network_factory, subnet_factory)

        port = port_factory(subnet_uuid=subnet["uuid"])
        port["provenance"] = "generated"
        # RESTAlchemy 16 answers a write to a read-only field with
        # `FieldPermissionError`, which is a 403.
        with pytest.raises(bazooka_exc.ForbiddenError):
            client.post(self._url(client), json=port)

    def test_create_port_simple_with_security_groups(
        self,
        user_api_client,
        auth_user_admin,
        network_factory,
        subnet_factory,
        port_factory,
        security_group_factory,
    ):
        client = user_api_client(auth_user_admin)
        subnet = self._subnet(client, network_factory, subnet_factory)

        sg_ref = sys_uuid.UUID(
            self._security_group(client, security_group_factory)["uuid"]
        )
        port = port_factory(subnet_uuid=subnet["uuid"], dhcp=True, dns=False)
        output = client.post(self._url(client), json=port).json()
        client.post(
            client.build_collection_uri(["network", "port_security_groups"]),
            json={
                "project_id": str(c.ZERO_UUID),
                "port": output["uuid"],
                "security_group": str(sg_ref),
            },
        )
        output = client.get(
            client.build_resource_uri(["network", "ports", output["uuid"]])
        ).json()
        assert output["security_groups"] == [str(sg_ref)]
        assert output["dhcp"] is True and output["dns"] is False

    def test_update_port_security(
        self,
        user_api_client,
        auth_user_admin,
        network_factory,
        subnet_factory,
        port_factory,
    ):
        client = user_api_client(auth_user_admin)
        subnet = self._subnet(client, network_factory, subnet_factory)

        port = port_factory(subnet_uuid=subnet["uuid"])
        assert client.post(self._url(client), json=port).status_code == 201

        res_url = client.build_resource_uri(["network", "ports", port["uuid"]])
        response = client.put(res_url, json={"port_security": False})
        assert response.status_code == 200
        assert response.json()["port_security"] is False

    def test_a_ports_address_is_not_rewritten_by_an_update(
        self,
        user_api_client,
        auth_user_admin,
        network_factory,
        subnet_factory,
        port_factory,
    ):
        """The address goes to the subnet's DHCP and is what a door onto
        the node leads to; on the platform's legs it is IPAM's."""
        client = user_api_client(auth_user_admin)
        subnet = self._subnet(client, network_factory, subnet_factory)

        port = port_factory(
            subnet_uuid=subnet["uuid"], ipv4=netaddr.IPAddress("10.42.0.7")
        )
        assert client.post(self._url(client), json=port).status_code == 201

        res_url = client.build_resource_uri(["network", "ports", port["uuid"]])
        with pytest.raises(bazooka_exc.ForbiddenError):
            client.put(res_url, json={"ipv4": "10.42.0.8"})

    def test_list_and_delete_port(
        self,
        user_api_client,
        auth_user_admin,
        network_factory,
        subnet_factory,
        port_factory,
    ):
        client = user_api_client(auth_user_admin)
        subnet = self._subnet(client, network_factory, subnet_factory)

        port = port_factory(subnet_uuid=subnet["uuid"])
        assert client.post(self._url(client), json=port).status_code == 201

        listed = client.get(self._url(client))
        assert listed.status_code == 200
        assert any(item["uuid"] == port["uuid"] for item in listed.json())

        res_url = client.build_resource_uri(["network", "ports", port["uuid"]])
        assert client.delete(res_url).status_code == 204
        with pytest.raises(bazooka_exc.NotFoundError):
            client.get(res_url)

    def test_a_port_may_not_borrow_a_bit_from_another_network(
        self,
        user_api_client,
        auth_user_admin,
        network_factory,
        subnet_factory,
        port_factory,
    ):
        """A port's groups are held to its own network.

        Bits are allocated per network, so a group of another network is a forged
        membership in whoever holds that bit here.
        """
        client = user_api_client(auth_user_admin)
        subnet = self._subnet(client, network_factory, subnet_factory)

        elsewhere = network_factory(
            name="net-elsewhere", driver_spec={"driver": "ovs_evpn"}
        )
        net_url = client.build_collection_uri(["network", "networks"])
        assert client.post(net_url, json=elsewhere).status_code == 201
        group_url = client.build_collection_uri(
            ["network", "catalog", "identity_groups"]
        )
        response = client.post(
            group_url,
            json={
                "uuid": str(sys_uuid.uuid4()),
                "name": "over-there",
                "project_id": elsewhere["project_id"],
                "network": elsewhere["uuid"],
            },
        )
        assert response.status_code == 201, response.text
        borrowed = response.json()["uuid"]

        port = port_factory(
            subnet_uuid=subnet["uuid"],
        )
        with pytest.raises(bazooka_exc.BadRequestError) as exc:
            client.post(
                client.build_collection_uri(["network", "port_identity_groups"]),
                json={
                    "project_id": str(c.ZERO_UUID),
                    "port": client.post(self._url(client), json=port).json()["uuid"],
                    "identity_group": borrowed,
                },
            )
        assert "port's network" in exc.value.cause.response.text

        # A group of this network is exactly the same request, and it works.
        mine = client.post(
            group_url,
            json={
                "uuid": str(sys_uuid.uuid4()),
                "name": "right-here",
                "project_id": subnet["project_id"],
                "network": subnet["network"],
            },
        )
        assert mine.status_code == 201, mine.text
        port = port_factory(
            subnet_uuid=subnet["uuid"],
        )
        created = client.post(self._url(client), json=port).json()
        assert (
            client.post(
                client.build_collection_uri(["network", "port_identity_groups"]),
                json={
                    "project_id": str(c.ZERO_UUID),
                    "port": created["uuid"],
                    "identity_group": mine.json()["uuid"],
                },
            ).status_code
            == 201
        )

    def test_a_port_may_not_borrow_another_projects_group(
        self,
        user_api_client,
        auth_user_admin,
        network_factory,
        subnet_factory,
        port_factory,
    ):
        """The project boundary, as for every other citation."""
        from exordos_core.user_api.network.dm import models as net_models

        client = user_api_client(auth_user_admin)
        subnet = self._subnet(client, network_factory, subnet_factory)
        # A group of another project lives on that project's own network —
        # the two go together now, since a network's bits are its owner's.
        foreign_project = sys_uuid.uuid4()
        foreign_network = compute_models.Network(
            uuid=sys_uuid.uuid4(),
            name="someone-elses-network",
            project_id=foreign_project,
            driver_spec={"driver": "ovs_evpn"},
        )
        foreign_network.insert()
        foreign = net_models.IdentityGroup(
            uuid=sys_uuid.uuid4(),
            name="someone-elses-group",
            project_id=foreign_project,
            network=foreign_network.uuid,
            identity=4,
        )
        foreign.insert()
        try:
            port = port_factory(
                subnet_uuid=subnet["uuid"],
            )
            with pytest.raises(bazooka_exc.BadRequestError) as exc:
                created = client.post(self._url(client), json=port).json()
                client.post(
                    client.build_collection_uri(["network", "port_identity_groups"]),
                    json={
                        "project_id": str(c.ZERO_UUID),
                        "port": created["uuid"],
                        "identity_group": str(foreign.uuid),
                    },
                )
            assert "another project" in exc.value.cause.response.text
        finally:
            foreign.delete()
            foreign_network.delete()
