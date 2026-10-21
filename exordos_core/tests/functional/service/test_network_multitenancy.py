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

"""The subnet tenancy boundary on address allocation.

Model-level: the boundary lives in ``Address._resolve_subnet`` and so holds on
every write path (HTTP and the manifest/db-back agent), independent of IAM.
"""

import uuid as sys_uuid

from gcl_sdk.infra.dm import models as infra_models
import netaddr
import pytest

from exordos_core.common import exceptions as ex_exceptions
from exordos_core.compute.dm import models as compute_models
from exordos_core.user_api.network.dm import models as net_models


def _network(project_id, access="private"):
    net = compute_models.Network(
        name="mt-net",
        driver_spec={"driver": "ovs_evpn"},
        project_id=project_id,
        access=access,
    )
    net.insert()
    return net


def _subnet(network, project_id):
    # A fresh /24 per call (random third octet) keeps allocations from
    # colliding across tests sharing a worker database.
    cidr = netaddr.IPNetwork(f"10.{sys_uuid.uuid4().int % 250}.0.0/24")
    subnet = compute_models.Subnet(
        network=network.uuid,
        cidr=cidr,
        project_id=project_id,
        dhcp=False,
    )
    subnet.insert()
    return subnet


@pytest.mark.usefixtures("user_api")
class TestAddressTenancyBoundary:
    def test_cannot_allocate_in_another_projects_private_subnet(self):
        """The cross-tenant IPAM DoS: refused."""
        owner, intruder = sys_uuid.uuid4(), sys_uuid.uuid4()
        subnet = _subnet(_network(owner, "private"), owner)

        addr = net_models.Address(subnet=subnet.uuid, project_id=intruder)
        with pytest.raises(ex_exceptions.ValidateException) as exc:
            addr.insert()
        assert "another project" in str(exc.value)

    def test_cannot_pin_an_explicit_address_in_a_private_foreign_subnet(self):
        owner, intruder = sys_uuid.uuid4(), sys_uuid.uuid4()
        subnet = _subnet(_network(owner, "private"), owner)

        addr = net_models.Address(
            subnet=subnet.uuid, project_id=intruder, address="10.0.0.5"
        )
        with pytest.raises(ex_exceptions.ValidateException):
            addr.insert()

    def test_can_allocate_in_a_public_subnet_of_another_project(self):
        """Publishing a network is exactly what lets others allocate in it."""
        owner, tenant = sys_uuid.uuid4(), sys_uuid.uuid4()
        subnet = _subnet(_network(owner, "public"), owner)

        addr = net_models.Address(subnet=subnet.uuid, project_id=tenant)
        addr.insert()
        assert addr.address is not None
        assert netaddr.IPAddress(addr.address) in subnet.cidr

    def test_owner_always_allocates_in_its_own_subnet(self):
        owner = sys_uuid.uuid4()
        subnet = _subnet(_network(owner, "private"), owner)

        addr = net_models.Address(subnet=subnet.uuid, project_id=owner)
        addr.insert()
        assert addr.address is not None

    def test_missing_subnet_is_refused(self):
        addr = net_models.Address(subnet=sys_uuid.uuid4(), project_id=sys_uuid.uuid4())
        with pytest.raises(ex_exceptions.ValidateException) as exc:
            addr.insert()
        assert "does not exist" in str(exc.value)


class TestNetworkAccessEnum:
    def test_projects_access_value_is_rejected(self):
        """Only private/public remain; the half-built 'projects' is gone."""
        with pytest.raises(Exception):
            compute_models.Network(
                name="bad",
                driver_spec={"driver": "ovs_evpn"},
                project_id=sys_uuid.uuid4(),
                access="projects",
            )


@pytest.mark.usefixtures("user_api")
class TestNetworkPermissionsSeed:
    """Network special permissions are enforceable three-part names.

    The enforcer parses every permission as ``service.res.perm``; a two-part
    name could never be granted.
    """

    def _names(self):
        from exordos_core.user_api.iam.dm import models as iam_models

        return {p.name for p in iam_models.Permission.objects.get_all()}

    def test_special_permissions_are_enforceable_and_dead_ones_removed(self):
        names = self._names()
        assert "network.network.share" in names
        assert "network.address.explicit_address" in names
        assert "network.share" not in names
        assert "network.explicit_address" not in names
        assert "network.route" not in names


def _pool(project_id, driver="flat_bridge", access="private", shared_pool=False):
    net = compute_models.Network(
        name="mt-pool",
        driver_spec={"driver": driver},
        project_id=project_id,
        access=access,
    )
    net.insert()
    pool = compute_models.Subnet(
        network=net.uuid,
        cidr=netaddr.IPNetwork(f"198.18.{sys_uuid.uuid4().int % 250}.0/24"),
        project_id=project_id,
        dhcp=False,
        placeable=False,
        floating_pool=True,
        shared_pool=shared_pool,
    )
    pool.insert()
    return pool


def _floating_address(project_id, subnet, pool):
    port = compute_models.Port(
        subnet=subnet.uuid,
        project_id=project_id,
        mac=compute_models.Port.generate_mac(),
    )
    port.insert()
    return net_models.Address(
        subnet=pool,
        project_id=project_id,
        kind="floating",
        port=port.uuid,
        owner_port=port.uuid,
    )


@pytest.mark.usefixtures("user_api")
class TestFloatingPools:
    """A floating address comes from a pool no tenant could have made.

    The hypervisor answers for it on the underlay and opens the fabric guard
    to it, so an overlay subnet a tenant declared with a management CIDR
    must not be one.
    """

    def _own_underlay_lookalike(self, tenant):
        net = _network(tenant, "private")
        subnet = compute_models.Subnet(
            network=net.uuid,
            cidr=netaddr.IPNetwork("192.168.77.0/24"),
            project_id=tenant,
            dhcp=False,
            placeable=False,
            shared_pool=True,
        )
        subnet.insert()
        return net, subnet

    def test_floating_allocation_from_a_tenants_own_overlay_subnet_is_refused(self):
        tenant = sys_uuid.uuid4()
        net, subnet = self._own_underlay_lookalike(tenant)
        leg = _subnet(net, tenant)

        port = _floating_address(tenant, leg, subnet.uuid)
        with pytest.raises(ex_exceptions.ValidateException) as exc:
            port.insert()
        assert "not a floating pool" in str(exc.value)

    def test_a_reservation_cannot_be_used_as_a_floating_binding(self):
        tenant = sys_uuid.uuid4()
        net, subnet = self._own_underlay_lookalike(tenant)
        leg = _subnet(net, tenant)
        port = compute_models.Port(subnet=leg.uuid, project_id=tenant)
        port.insert()
        address = net_models.Address(
            subnet=subnet.uuid, project_id=tenant, address="192.168.77.1"
        )
        address.insert()
        address.port = port.uuid
        with pytest.raises(
            ex_exceptions.ValidateException, match="reservation cannot bind"
        ):
            address.update()

    def test_floating_allocation_from_a_missing_subnet_is_refused(self):
        tenant = sys_uuid.uuid4()
        leg = _subnet(_network(tenant), tenant)

        port = _floating_address(tenant, leg, sys_uuid.uuid4())
        with pytest.raises(ex_exceptions.ValidateException) as exc:
            port.insert()
        assert "does not exist" in str(exc.value)

    def test_a_shared_pool_of_the_operator_serves_a_tenant(self):
        operator, tenant = sys_uuid.uuid4(), sys_uuid.uuid4()
        pool = _pool(operator, shared_pool=True)
        leg = _subnet(_network(tenant), tenant)

        _floating_address(tenant, leg, pool.uuid).insert()

    def test_a_pool_on_a_published_overlay_serves_a_tenant(self):
        operator, tenant = sys_uuid.uuid4(), sys_uuid.uuid4()
        pool = _pool(operator, driver="ovs_evpn", access="public")
        leg = _subnet(_network(tenant), tenant)
        address = net_models.Address(
            subnet=pool.uuid, project_id=tenant, kind="floating"
        )
        address.insert()

        port = compute_models.Port(subnet=leg.uuid, project_id=tenant)
        port.insert()
        address.port = port.uuid
        address.update()

    def test_an_operators_private_pool_is_not_a_tenants(self):
        operator, tenant = sys_uuid.uuid4(), sys_uuid.uuid4()
        pool = _pool(operator)
        leg = _subnet(_network(tenant), tenant)

        port = _floating_address(tenant, leg, pool.uuid)
        with pytest.raises(ex_exceptions.ValidateException) as exc:
            port.insert()
        assert "another project" in str(exc.value)

    def test_only_a_pools_floating_addresses_open_the_fabric_guard(self):
        """The ledger row is what the guard trusts, and something that writes
        the model directly can put `floating` on any of them."""
        from exordos_core.network.driver import evpn

        tenant = sys_uuid.uuid4()
        _, subnet = self._own_underlay_lookalike(tenant)
        forged = net_models.Address(
            subnet=subnet.uuid,
            project_id=tenant,
            address="192.168.77.9",
        )
        forged.insert()
        pool = _pool(sys_uuid.uuid4(), shared_pool=True)
        real = net_models.Address(subnet=pool.uuid, project_id=tenant, kind="floating")
        real.insert()

        published = evpn._published_addresses()
        assert "192.168.77.9" not in published
        assert str(real.address) in published


def _flat(project_id):
    net = compute_models.Network(
        name="mt-flat",
        driver_spec={"driver": "flat_bridge"},
        project_id=project_id,
    )
    net.insert()
    subnet = compute_models.Subnet(
        network=net.uuid,
        cidr=netaddr.IPNetwork(f"172.16.{sys_uuid.uuid4().int % 250}.0/24"),
        project_id=project_id,
        dhcp=False,
    )
    subnet.insert()
    return subnet


def _port_on(subnet, project_id, ipv4=None, **kwargs):
    return compute_models.Port(
        subnet=subnet.uuid,
        project_id=project_id,
        mac=compute_models.Port.generate_mac(),
        ipv4=netaddr.IPAddress(ipv4) if ipv4 else None,
        **kwargs,
    )


@pytest.mark.usefixtures("user_api")
class TestPortsOnFlatSubnets:
    """A flat subnet is the operator's metal: its addresses go to ISC dhcpd."""

    def test_a_hand_made_port_on_the_operators_flat_subnet_is_refused(self):
        operator, tenant = sys_uuid.uuid4(), sys_uuid.uuid4()
        subnet = _flat(operator)

        with pytest.raises(ex_exceptions.ValidateException) as exc:
            _port_on(subnet, tenant).insert()
        assert "no accessible subnet" in str(exc.value)

    def test_the_platforms_own_leg_there_is_let_through(self):
        """A node's generated port keeps the node's project on the operator's
        subnet — the placement every flat installation has."""
        operator, tenant = sys_uuid.uuid4(), sys_uuid.uuid4()
        subnet = _flat(operator)

        node = compute_models.Node(
            uuid=sys_uuid.uuid4(),
            project_id=tenant,
            cores=1,
            ram=128,
            disk_spec=infra_models.DisksSpec(disks=[]),
            default_network={},
        )
        node.insert()
        _port_on(subnet, tenant, node=node.uuid, owner_node=node.uuid).insert()

    def test_an_address_outside_the_subnet_is_refused(self):
        owner = sys_uuid.uuid4()
        subnet = _flat(owner)

        with pytest.raises(ex_exceptions.ValidateException) as exc:
            _port_on(subnet, owner, ipv4="192.0.2.1").insert()
        assert "is not in" in str(exc.value)

    def test_an_address_another_port_holds_is_refused(self):
        owner = sys_uuid.uuid4()
        subnet = _flat(owner)
        held = str(subnet.cidr[10])
        _port_on(subnet, owner, ipv4=held).insert()

        with pytest.raises(ex_exceptions.ValidateException) as exc:
            _port_on(subnet, owner, ipv4=held).insert()
        assert "already port" in str(exc.value)
