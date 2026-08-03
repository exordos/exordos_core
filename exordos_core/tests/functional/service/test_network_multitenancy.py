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

"""The subnet tenancy boundary on address allocation (SDN CP API §8).

Model-level: the boundary lives in ``Address._resolve_subnet`` and so holds on
every write path (HTTP and the manifest/db-back agent), independent of IAM.
"""

import uuid as sys_uuid

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
    """Migration 0069 fixed the §8 special permissions.

    The enforcer parses every permission as three-part ``service.res.perm``, so
    the two-part names 0068 seeded could never be granted — they are replaced
    with enforceable forms and the dead rows removed.
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
