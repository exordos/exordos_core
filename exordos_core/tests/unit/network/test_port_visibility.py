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

"""Who may see a port that is not theirs.

A port belongs to its node's project. An `ovs_evpn` subnet is the tenant's
own segment, so the tenant sees foreign ports in it (e.g. a managed
service's node); otherwise addresses vanish and `Subnet.delete` refuses
over ports they cannot find.
"""

from unittest import mock
import uuid as sys_uuid

import pytest

from exordos_core.compute import constants as nc
from exordos_core.user_api.network.api import controllers


TENANT = sys_uuid.uuid4()
SERVICE = sys_uuid.uuid4()


@pytest.fixture
def controller():
    controller = controllers.PortController.__new__(controllers.PortController)
    controller._ctx_project_id = TENANT
    controller._enforce = mock.Mock(return_value=True)
    return controller


def _network(driver):
    return mock.MagicMock(uuid=sys_uuid.uuid4(), driver_spec={"driver": driver})


def _subnet(uuid=None, network=None):
    return mock.MagicMock(uuid=uuid or sys_uuid.uuid4(), network=network)


def _port(project_id, subnet):
    return mock.MagicMock(project_id=project_id, subnet=subnet)


class _World:
    """The networks and subnets the controller will find."""

    def __init__(self, networks, subnets):
        self.networks, self.subnets = networks, subnets

    def __enter__(self):
        self._patch = mock.patch.object(controllers, "compute_models")
        models = self._patch.__enter__()
        models.Network.objects.get_all.return_value = self.networks
        models.Subnet.objects.get_all.return_value = self.subnets
        return models

    def __exit__(self, *exc):
        self._patch.__exit__(*exc)


def test_an_overlay_subnet_of_mine_makes_its_ports_visible(controller):
    overlay = _network(nc.OVERLAY_DRIVER)
    mine = _subnet(network=overlay.uuid)
    with _World([overlay], [mine]):
        assert controller._own_overlay_subnets() == [mine.uuid]


def test_a_flat_subnet_of_mine_does_not(controller):
    """Co-tenancy on shared metal is a property of the wire, not a statement
    about tenancy — and widening it would change what every existing
    installation returns."""
    flat = _network("flat_bridge")
    with _World([flat], [_subnet(network=flat.uuid)]):
        assert controller._own_overlay_subnets() == []


def test_a_caller_with_no_subnets_asks_nothing_of_the_networks(controller):
    """This runs on every listing, so what it asks is bounded by the caller.

    The question is only ever about the networks their own subnets are on,
    and a caller with no subnets has none to ask about.
    """
    with _World([_network(nc.OVERLAY_DRIVER)], []) as models:
        assert controller._own_overlay_subnets() == []
        models.Network.objects.get_all.assert_not_called()


def test_the_scope_keeps_the_caller_s_own_filters(controller):
    overlay = _network(nc.OVERLAY_DRIVER)
    with _World([overlay], [_subnet()]):
        scope = controller._visible_scope({"status": "ACTIVE"})

    # An AND of (caller filters, OR(own project, my overlay subnets)).
    assert scope is not None
    rendered = str(scope)
    assert "status" in rendered


def test_with_no_overlay_the_scope_is_the_plain_project_one(controller):
    with _World([_network("flat_bridge")], []):
        scope = controller._visible_scope(None)
    assert str(TENANT) in str(scope)


class TestGetOneByOne:
    """Listed but not fetchable would be a tease."""

    def test_my_own_port_is_returned_without_asking_about_subnets(self, controller):
        port = _port(TENANT, _subnet().uuid)
        with (
            mock.patch.object(
                controllers.PortController, "_own_overlay_subnets"
            ) as own,
            mock.patch.object(
                controllers.ra_controllers.BaseResourceControllerPaginated,
                "get",
                return_value=port,
            ),
        ):
            assert controller.get(sys_uuid.uuid4()) is port
            own.assert_not_called()

    def test_a_foreign_port_on_my_overlay_subnet_is_returned(self, controller):
        subnet = _subnet()
        port = _port(SERVICE, subnet.uuid)
        with (
            mock.patch.object(
                controllers.PortController,
                "_own_overlay_subnets",
                return_value=[subnet.uuid],
            ),
            mock.patch.object(
                controllers.ra_controllers.BaseResourceControllerPaginated,
                "get",
                return_value=port,
            ),
        ):
            assert controller.get(port_uuid := sys_uuid.uuid4()) is port
            assert port_uuid is not None

    def test_a_foreign_port_elsewhere_falls_back_to_the_narrow_lookup(self, controller):
        """ "Not yours" must fail exactly as it always did, not in a new voice."""
        port = _port(SERVICE, _subnet().uuid)
        with (
            mock.patch.object(
                controllers.PortController, "_own_overlay_subnets", return_value=[]
            ),
            mock.patch.object(
                controllers.ra_controllers.BaseResourceControllerPaginated,
                "get",
                return_value=port,
            ),
            mock.patch.object(
                controllers.iam_controllers.PolicyBasedController,
                "get",
                side_effect=LookupError("record not found"),
                autospec=True,
            ) as narrow,
        ):
            with pytest.raises(LookupError):
                controller.get(sys_uuid.uuid4())
            narrow.assert_called_once()

    def test_an_admin_asks_the_narrow_way_and_by_the_name_it_expects(self):
        """No project scope, no widening, and `get` takes the uuid by keyword.

        `PolicyBasedController.get` builds the lookup from keywords; the mock is
        specced against the real signature so a positional call fails here.
        """
        admin = controllers.PortController.__new__(controllers.PortController)
        admin._ctx_project_id = None
        admin._enforce = mock.Mock(return_value=True)
        port = _port(SERVICE, _subnet().uuid)
        with mock.patch.object(
            controllers.iam_controllers.PolicyBasedController,
            "get",
            return_value=port,
            autospec=True,
        ) as narrow:
            assert admin.get(port_uuid := sys_uuid.uuid4()) is port

        (_, kwargs) = narrow.call_args[0], narrow.call_args[1]
        assert kwargs["uuid"] == port_uuid
