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

from exordos_core.compute.dm import models as compute_models


class TestPortApi:
    """SDN CP API phase 3: thin port with polymorphic config kind (§5)."""

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
        assert client.post(url, json=sg).status_code == 201
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
            config=compute_models.PortSimpleKind(security_groups=[foreign.uuid]),
        )
        with pytest.raises(bazooka_exc.BadRequestError) as exc:
            client.post(self._url(client), json=port)
        assert "another project" in str(exc.value.cause.response.text)

        # ... and the refusal leaves the owner's object deletable
        foreign.delete()

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
        config = compute_models.PortSimpleKind(
            security_groups=[sg_ref], dhcp=True, dns=False
        )
        port = port_factory(subnet_uuid=subnet["uuid"], config=config)
        output = client.post(self._url(client), json=port).json()
        assert output["config"]["kind"] == "simple"
        assert output["config"]["security_groups"] == [str(sg_ref)]
        assert output["config"]["dhcp"] is True

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
        """Alone among the port's citations, its groups were never checked —
        and they are the one that decides what the fabric stamps on the
        guest's packets.

        A bit is allocated per network, so naming a group is naming a
        *number* and the number means whatever the port's own network says it
        means. A group of the caller's own project on another network is
        therefore a forged membership in whoever holds that bit here, and
        every rule admitting that group admits this guest.
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
            config=compute_models.PortSimpleKind(
                identity_groups=[sys_uuid.UUID(borrowed)]
            ),
        )
        with pytest.raises(bazooka_exc.BadRequestError) as exc:
            client.post(self._url(client), json=port)
        assert "another network" in exc.value.cause.response.text

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
            config=compute_models.PortSimpleKind(
                identity_groups=[sys_uuid.UUID(mine.json()["uuid"])]
            ),
        )
        assert client.post(self._url(client), json=port).status_code == 201

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
                config=compute_models.PortSimpleKind(identity_groups=[foreign.uuid]),
            )
            with pytest.raises(bazooka_exc.BadRequestError) as exc:
                client.post(self._url(client), json=port)
            assert "another project" in exc.value.cause.response.text
        finally:
            foreign.delete()
            foreign_network.delete()
