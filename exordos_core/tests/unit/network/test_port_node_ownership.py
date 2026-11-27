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

"""Whose node a port may be a leg of."""

from unittest import mock
import uuid as sys_uuid

import pytest

from exordos_core.common import exceptions as ex_exceptions
from exordos_core.compute.dm import models as compute_models

TENANT = sys_uuid.uuid4()
VICTIM = sys_uuid.uuid4()


def _port(project_id=TENANT, owned=False, node=None):
    """A stand-in for checking port ownership without storage."""
    node = node if node is not None else sys_uuid.uuid4()
    return mock.MagicMock(
        project_id=project_id, owner_node=node if owned else None, node=node
    )


def _nodes(node):
    objects = mock.MagicMock()
    objects.get_one_or_none.return_value = node
    return mock.patch.object(compute_models.Node, "objects", objects)


def test_a_port_on_my_own_node_is_fine():
    port = _port()
    with _nodes(mock.MagicMock(uuid=port.node, project_id=TENANT)):
        compute_models.Port._validate_node_ownership(port)  # must not raise


def test_a_port_on_somebody_elses_node_is_refused():
    """The hole this closes: the reconcile loop acts on whatever the row
    says, so a port naming another project's node writes that node's
    `default_network`, schedules a slice to the hypervisor serving it, and
    offers itself as the interface its machine comes up with."""
    port = _port()
    with _nodes(mock.MagicMock(uuid=port.node, project_id=VICTIM)):
        with pytest.raises(ex_exceptions.ValidateException) as exc:
            compute_models.Port._validate_node_ownership(port)
    assert "another project" in str(exc.value)


def test_a_port_naming_no_node_is_not_this_check_s_business():
    port = _port(node=None)
    port.node = None
    with _nodes(None):
        compute_models.Port._validate_node_ownership(port)  # must not raise


def test_a_port_naming_a_node_that_does_not_exist_is_refused():
    port = _port()
    with _nodes(None):
        with pytest.raises(ex_exceptions.ValidateException):
            compute_models.Port._validate_node_ownership(port)


def test_the_installations_own_port_for_a_bare_metal_node_still_passes():
    """`_get_new_hw_ports` gives a discovered machine a port in the project of
    the subnet it was found on, not in its node's. Only the platform may
    assign `owner_node` to such a port."""
    port = _port(owned=True)
    with _nodes(mock.MagicMock(uuid=port.node, project_id=VICTIM, node_type="HW")):
        compute_models.Port._validate_node_ownership(port)  # must not raise
