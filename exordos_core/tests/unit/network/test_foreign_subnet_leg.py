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

"""Putting a node on somebody else's subnet.

The controller asks *may you* (it has the caller); the model asks whether a
declaration is being executed, so a hand-made port in a foreign private
subnet is still refused.
"""

from unittest import mock
import uuid as sys_uuid

from gcl_iam.api import controllers as iam_controllers
import pytest

from exordos_core.common import exceptions as ex_exceptions
from exordos_core.compute.dm import models as compute_models
from exordos_core.user_api.compute.api import controllers

TENANT = sys_uuid.uuid4()
SERVICE = sys_uuid.uuid4()


def _leg(subnet=None, network=None, kind="subnet"):
    leg = mock.MagicMock()
    leg.KIND = kind
    leg.subnet, leg.network = subnet, network
    return leg


class TestTheControllerAsksMayYou:
    @pytest.fixture
    def controller(self):
        controller = controllers.NodesController.__new__(controllers.NodesController)
        controller._ctx_project_id = SERVICE
        controller._enforce = mock.Mock(return_value=True)
        return controller

    def _owned_by(self, project_id):
        objects = mock.MagicMock()
        objects.get_one_or_none.return_value = mock.MagicMock(project_id=project_id)
        return mock.patch.object(controllers.models.Subnet, "objects", objects)

    def test_a_leg_in_my_own_subnet_asks_for_nothing(self, controller):
        with self._owned_by(SERVICE):
            controller._enforce_foreign_subnets([_leg(subnet=sys_uuid.uuid4())])
        controller._enforce.assert_not_called()

    def test_a_leg_in_someone_else_s_subnet_needs_the_permission(self, controller):
        with self._owned_by(TENANT):
            controller._enforce_foreign_subnets([_leg(subnet=sys_uuid.uuid4())])
        controller._enforce.assert_called_once_with("foreign_subnet")

    def test_the_permission_is_asked_for_once_however_many_legs(self, controller):
        with self._owned_by(TENANT):
            controller._enforce_foreign_subnets(
                [_leg(subnet=sys_uuid.uuid4()), _leg(subnet=sys_uuid.uuid4())]
            )
        assert controller._enforce.call_count == 1

    def test_an_adopted_port_is_not_this_gate_s_business(self, controller):
        """Adopting a port already refuses one of another project."""
        controller._enforce_foreign_subnets([_leg(kind="port")])
        controller._enforce.assert_not_called()

    def test_an_unscoped_caller_is_left_alone(self, controller):
        controller._ctx_project_id = None
        controller._enforce_foreign_subnets([_leg(subnet=sys_uuid.uuid4())])
        controller._enforce.assert_not_called()


class TestBothWaysInAreGated:
    """`interfaces` is editable, so an edit meets the same gate as a create."""

    def _controller(self):
        controller = controllers.NodesController.__new__(controllers.NodesController)
        controller._ctx_project_id = SERVICE
        controller._enforce = mock.Mock(return_value=True)
        return controller

    @staticmethod
    def _foreign_subnet():
        objects = mock.MagicMock()
        objects.get_one_or_none.return_value = mock.MagicMock(project_id=TENANT)
        return mock.patch.object(controllers.models.Subnet, "objects", objects)

    def test_an_edit_that_moves_a_leg_is_asked_the_same_question(self):
        controller = self._controller()
        legs = [_leg(subnet=sys_uuid.uuid4())]
        with (
            self._foreign_subnet(),
            mock.patch.object(
                iam_controllers.PolicyBasedController, "update", return_value=None
            ),
        ):
            controllers.NodesController.update(
                controller, sys_uuid.uuid4(), interfaces=legs
            )
        controller._enforce.assert_called_once_with("foreign_subnet")

    def test_an_edit_that_says_nothing_about_legs_asks_nothing(self):
        controller = self._controller()
        with (
            self._foreign_subnet(),
            mock.patch.object(
                iam_controllers.PolicyBasedController, "update", return_value=None
            ),
        ):
            controllers.NodesController.update(controller, sys_uuid.uuid4(), cores=4)
        controller._enforce.assert_not_called()

    def test_a_set_is_held_to_it_on_an_edit_too(self):
        controller = controllers.NodeSetsController.__new__(
            controllers.NodeSetsController
        )
        controller._ctx_project_id = SERVICE
        controller._enforce = mock.Mock(return_value=True)
        legs = [_leg(subnet=sys_uuid.uuid4())]
        with (
            mock.patch.object(
                controller,
                "get",
                return_value=mock.MagicMock(interfaces=None, default_network=None),
            ),
            self._foreign_subnet(),
            mock.patch.object(
                iam_controllers.PolicyBasedController, "update", return_value=None
            ),
        ):
            controllers.NodeSetsController.update(
                controller, sys_uuid.uuid4(), interfaces=legs
            )
        controller._enforce.assert_called_once_with("foreign_subnet")


class TestTheModelAsksIsThereADeclaration:
    """`_validate_subnet_access` runs in a reconcile loop with no IAM at all."""

    def _port(self, owned=True):
        """A stand-in, called unbound.

        Not a real `Port`: restalchemy descriptors share one slot on an instance
        built with `__new__`, so only the last assigned field would read true.
        """
        port = mock.MagicMock(
            project_id=SERVICE,
            subnet=sys_uuid.uuid4(),
            owner_node=None,
            node=sys_uuid.uuid4(),
        )
        port.owner_node = port.node if owned else None
        # The real one, or the mock answers "declared" to everything and the
        # test proves nothing.
        port._declared_by_its_node = lambda subnet: (
            compute_models.Port._declared_by_its_node(port, subnet)
        )
        return port

    def _world(self, port, node_interfaces, access="private"):
        """One subnet of the tenant's, on a private overlay, and a node whose
        declaration is whatever the test says."""
        subnet = mock.MagicMock(
            uuid=port.subnet, project_id=TENANT, network=sys_uuid.uuid4()
        )
        network = mock.MagicMock(driver_spec={"driver": "ovs_evpn"}, access=access)

        def _objects(value):
            objects = mock.MagicMock()
            objects.get_one_or_none.return_value = value
            return objects

        return (
            mock.patch.object(compute_models.Subnet, "objects", _objects(subnet)),
            mock.patch.object(compute_models.Network, "objects", _objects(network)),
            mock.patch.object(
                compute_models.Node,
                "objects",
                _objects(mock.MagicMock(interfaces=node_interfaces)),
            ),
            subnet,
        )

    def test_a_generated_port_its_node_declared_is_let_through(self):
        port = self._port()
        subnet_uuid = port.subnet
        subnets, networks, nodes, subnet = self._world(port, [_leg(subnet=subnet_uuid)])
        with subnets, networks, nodes:
            compute_models.Port._validate_subnet_access(port, subnet)  # must not raise

    def test_a_port_nobody_declared_is_still_refused(self):
        """The cross-tenant hole this boundary was built for."""
        port = self._port()
        subnets, networks, nodes, subnet = self._world(
            port, [_leg(subnet=sys_uuid.uuid4())]
        )
        with subnets, networks, nodes:
            with pytest.raises(ex_exceptions.ValidateException):
                compute_models.Port._validate_subnet_access(port, subnet)

    def test_a_released_port_keeps_its_declared_placement(self):
        port = self._port(owned=False)
        subnets, networks, nodes, subnet = self._world(port, [_leg(subnet=port.subnet)])
        with subnets, networks, nodes:
            compute_models.Port._validate_subnet_access(port, subnet)

    def test_a_leg_that_named_the_network_counts_too(self):
        port = self._port()
        network_uuid = sys_uuid.uuid4()
        subnets, networks, nodes, subnet = self._world(
            port, [_leg(network=network_uuid)]
        )
        subnet.network = network_uuid
        with subnets, networks, nodes:
            compute_models.Port._validate_subnet_access(port, subnet)  # must not raise
