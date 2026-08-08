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

"""Cross-project behaviour of a published (access=public) network (§8).

``TestSharedNetwork`` drives the allocation boundary and the explicit-address
gate from a default-project user against *foreign* networks/subnets seeded
directly through the models under another project id. ``TestSharedNetworkCrossProject``
uses the ``*_p1_user`` fixtures — two real projects with project-scoped tokens —
to exercise the visibility filter, which keys on a non-null caller project.
"""

import uuid as sys_uuid

from bazooka import exceptions as bazooka_exc
import netaddr
import pytest

from exordos_core.common import constants as c
from exordos_core.compute.dm import models as compute_models

# The default project the test users act in.
CALLER_PROJECT = str(c.SERVICE_PROJECT_ID)


def _foreign_subnet(access, cidr, shared_pool=False):
    """A subnet owned by some other project, seeded via the models."""
    owner = sys_uuid.uuid4()
    net = compute_models.Network(
        name=f"foreign-{owner.hex[:8]}",
        driver_spec={"driver": "ovs_evpn"},
        project_id=owner,
        access=access,
    )
    net.insert()
    subnet = compute_models.Subnet(
        network=net.uuid,
        cidr=netaddr.IPNetwork(cidr),
        project_id=owner,
        dhcp=False,
        shared_pool=shared_pool,
    )
    subnet.insert()
    return subnet


def _foreign_network(access="private"):
    """An ``ovs_evpn`` network owned by some other project."""
    owner = sys_uuid.uuid4()
    net = compute_models.Network(
        name=f"foreign-net-{owner.hex[:8]}",
        driver_spec={"driver": "ovs_evpn"},
        project_id=owner,
        access=access,
    )
    net.insert()
    return net


def _addresses(client):
    return client.build_collection_uri(["network", "catalog", "addresses"])


def _address_item(client, uuid):
    return client.build_resource_uri(["network", "catalog", "addresses", uuid])


@pytest.mark.usefixtures("user_api")
class TestSharedNetwork:
    def test_cannot_allocate_in_a_foreign_private_subnet(
        self, user_api_client, auth_test1_user
    ):
        private = _foreign_subnet("private", "10.73.0.0/24")
        client = user_api_client(
            auth_test1_user, permissions=["network.address.create"]
        )
        payload = {"subnet": str(private.uuid), "project_id": CALLER_PROJECT}
        with pytest.raises(bazooka_exc.BadRequestError):
            client.post(_addresses(client), json=payload)

    def test_auto_allocation_in_a_public_subnet_needs_no_special_permission(
        self, user_api_client, auth_test1_user
    ):
        public = _foreign_subnet("public", "10.74.0.0/24")
        client = user_api_client(
            auth_test1_user, permissions=["network.address.create"]
        )
        resp = client.post(
            _addresses(client),
            json={"subnet": str(public.uuid), "project_id": CALLER_PROJECT},
        )
        assert resp.status_code == 201, resp.text
        assert netaddr.IPAddress(resp.json()["address"]) in public.cidr

    def test_a_shared_pool_is_allocatable_without_publishing_its_network(
        self, user_api_client, auth_test1_user
    ):
        """The narrow authorization an ingress pool needs.

        A floating address for a realm is allocated in the *port's* project
        while the pool belongs to the network's, so something has to say the
        pool is anybody's to draw from. Publishing the network says far more
        than that — every subnet of it, the range the installation hands to
        nodes included — so the pool says it about itself.
        """
        pool = _foreign_subnet("private", "10.76.0.0/24", shared_pool=True)
        client = user_api_client(
            auth_test1_user, permissions=["network.address.create"]
        )
        resp = client.post(
            _addresses(client),
            json={"subnet": str(pool.uuid), "project_id": CALLER_PROJECT},
        )
        assert resp.status_code == 201, resp.text
        assert netaddr.IPAddress(resp.json()["address"]) in pool.cidr

    def test_a_shared_pool_opens_itself_and_not_its_neighbours(
        self, user_api_client, auth_test1_user
    ):
        """One subnet of a network being a pool is not the network being
        published: the subnet beside it is as private as it was."""
        pool = _foreign_subnet("private", "10.77.0.0/24", shared_pool=True)
        neighbour = compute_models.Subnet(
            network=pool.network,
            cidr=netaddr.IPNetwork("10.78.0.0/24"),
            project_id=pool.project_id,
            dhcp=False,
        )
        neighbour.insert()
        client = user_api_client(
            auth_test1_user, permissions=["network.address.create"]
        )
        with pytest.raises(bazooka_exc.BadRequestError):
            client.post(
                _addresses(client),
                json={"subnet": str(neighbour.uuid), "project_id": CALLER_PROJECT},
            )

    def test_pinning_an_address_in_a_foreign_public_subnet_needs_permission(
        self, user_api_client, auth_test1_user
    ):
        public = _foreign_subnet("public", "10.75.0.0/24")
        pinned = {
            "subnet": str(public.uuid),
            "address": "10.75.0.9",
            "project_id": CALLER_PROJECT,
        }

        # address.create alone is not enough to pin a *specific* address in a
        # subnet the caller does not own.
        creator = user_api_client(
            auth_test1_user, permissions=["network.address.create"]
        )
        with pytest.raises(bazooka_exc.ForbiddenError):
            creator.post(_addresses(creator), json=pinned)

        # With the explicit-address permission it goes through.
        pinner = user_api_client(
            auth_test1_user,
            permissions=[
                "network.address.create",
                "network.address.explicit_address",
            ],
        )
        resp = pinner.post(_addresses(pinner), json=pinned)
        assert resp.status_code == 201, resp.text
        assert resp.json()["address"] == "10.75.0.9"


@pytest.mark.usefixtures("user_api")
class TestCrossTenantWrites:
    """The isolation boundary must hold on every write path, not just address
    creation — a port's subnet, a subnet's network, and the address *update*
    path each used to be unchecked (UPDATE was weaker than CREATE)."""

    def _ports(self, client):
        return client.build_collection_uri(["network", "ports"])

    def test_a_port_cannot_plug_into_a_foreign_private_subnet(
        self, user_api_client, auth_test1_user, port_factory
    ):
        # Plugging a guest into another tenant's private overlay subnet would
        # drop it straight onto their isolated segment.
        private = _foreign_subnet("private", "10.76.0.0/24")
        client = user_api_client(auth_test1_user, permissions=["network.port.create"])
        port = port_factory(subnet_uuid=private.uuid)
        with pytest.raises(bazooka_exc.BadRequestError):
            client.post(self._ports(client), json=port)

    def test_a_subnet_cannot_cite_a_foreign_network(
        self, user_api_client, auth_test1_user, subnet_factory
    ):
        # Publishing a network lets others allocate in it, not add subnets to
        # it — a subnet must cite a network the caller owns.
        foreign = _foreign_network()
        client = user_api_client(auth_test1_user, permissions=["network.subnet.create"])
        sub = subnet_factory(network_uuid=foreign.uuid, cidr="10.79.0.0/24", dhcp=False)
        with pytest.raises(bazooka_exc.BadRequestError):
            client.post(client.build_collection_uri(["network", "subnets"]), json=sub)

    def test_an_address_update_cannot_reach_into_a_foreign_private_subnet(
        self, user_api_client, auth_user_admin
    ):
        # Allocate legitimately in a public subnet, then try to move the row
        # into a foreign *private* subnet by PUT — the model gate must refuse
        # it exactly as it refuses the same subnet at create time.
        public = _foreign_subnet("public", "10.77.0.0/24")
        private = _foreign_subnet("private", "10.78.0.0/24")
        client = user_api_client(auth_user_admin)
        created = client.post(
            _addresses(client),
            json={"subnet": str(public.uuid), "project_id": CALLER_PROJECT},
        )
        assert created.status_code == 201, created.text
        item = _address_item(client, created.json()["uuid"])
        with pytest.raises(bazooka_exc.BadRequestError):
            client.put(item, json={"subnet": str(private.uuid)})

    def test_an_address_update_pinning_in_a_foreign_public_subnet_needs_permission(
        self, user_api_client, auth_test1_user
    ):
        # The explicit-address permission gates pinning a specific address in a
        # foreign public subnet on update too, not only on create.
        public = _foreign_subnet("public", "10.80.0.0/24")
        client = user_api_client(
            auth_test1_user,
            permissions=["network.address.create", "network.address.update"],
        )
        created = client.post(
            _addresses(client),
            json={"subnet": str(public.uuid), "project_id": CALLER_PROJECT},
        )
        assert created.status_code == 201, created.text
        item = _address_item(client, created.json()["uuid"])
        with pytest.raises(bazooka_exc.ForbiddenError):
            client.put(item, json={"subnet": str(public.uuid), "address": "10.80.0.9"})


@pytest.mark.usefixtures("user_api")
class TestSharedNetworkCrossProject:
    """End-to-end across two real, distinct projects.

    The `*_p1_user` fixtures issue project-scoped tokens (ctx_project_id set),
    which is what the visibility filter keys on — a single default-project run
    could not exercise it.
    """

    def _publish(self, client, network_factory, subnet_factory, project, cidr, access):
        net = network_factory(
            driver_spec={"driver": "ovs_evpn"}, access=access, project_id=project
        )
        assert (
            client.post(
                client.build_collection_uri(["network", "networks"]), json=net
            ).status_code
            == 201
        )
        sub = subnet_factory(
            network_uuid=net["uuid"], cidr=cidr, dhcp=False, project_id=project
        )
        assert (
            client.post(
                client.build_collection_uri(["network", "subnets"]), json=sub
            ).status_code
            == 201
        )
        return sub

    def test_public_network_is_shared_end_to_end(
        self,
        user_api_client,
        auth_test1_p1_user,
        auth_test2_p1_user,
        network_factory,
        subnet_factory,
        address_factory,
    ):
        p1 = sys_uuid.UUID(auth_test1_p1_user.project_id)
        owner = user_api_client(
            auth_test1_p1_user,
            permissions=[
                "network.network.create",
                "network.network.read",
                "network.network.share",
                "network.subnet.create",
                "network.subnet.read",
            ],
            project_id=str(p1),
        )
        pub_sub = self._publish(
            owner, network_factory, subnet_factory, p1, "10.83.0.0/24", "public"
        )
        priv_sub = self._publish(
            owner, network_factory, subnet_factory, p1, "10.84.0.0/24", "private"
        )

        p2 = sys_uuid.UUID(auth_test2_p1_user.project_id)
        tenant = user_api_client(
            auth_test2_p1_user,
            permissions=[
                "network.subnet.read",
                "network.address.create",
                "network.address.read",
            ],
            project_id=str(p2),
        )

        # The tenant sees the owner's public subnet, never its private one.
        listed = tenant.get(tenant.build_collection_uri(["network", "subnets"])).json()
        uuids = {s["uuid"] for s in listed}
        assert pub_sub["uuid"] in uuids
        assert priv_sub["uuid"] not in uuids

        # …and allocates its own address inside the owner's public subnet.
        addr = address_factory(subnet_uuid=pub_sub["uuid"], project_id=p2)
        resp = tenant.post(_addresses(tenant), json=addr)
        assert resp.status_code == 201, resp.text
        assert resp.json()["project_id"] == str(p2)
