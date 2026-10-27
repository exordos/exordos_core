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

"""What the data plane answered, on the rows this driver writes itself.

Models in `network/evpn/dm/models.py` default `status` to `NEW`, and the
compiler rewrites a row only on a hash change, which excludes status; the
agent's answer has to be brought back separately.
"""

from unittest import mock
import uuid as sys_uuid

import pytest

from exordos_core.network.driver import evpn


def _row(kind, uuid, status, hash_, full_hash="f"):
    row = mock.MagicMock()
    row.kind, row.uuid, row.status = kind, uuid, status
    row.hash, row.full_hash = hash_, full_hash
    return row


@pytest.fixture
def driver():
    return evpn.OvsEvpnNetworkDriver.__new__(evpn.OvsEvpnNetworkDriver)


def _sweep(driver, targets, actual):
    with (
        mock.patch.object(evpn.ua_models, "TargetResource") as target_model,
        mock.patch.object(evpn.ua_models, "Resource") as actual_model,
        mock.patch.object(evpn.models.MachinePool, "objects") as pools,
        mock.patch(
            "exordos_core.network.contracts.applied_by_owner", return_value=True
        ),
    ):
        target_model.objects.get_all.return_value = targets
        actual_model.objects.get_all.return_value = actual
        pools.get_all.return_value = []
        driver._actualize_statuses()


def test_a_resource_the_data_plane_applied_stops_saying_new(driver):
    uuid = sys_uuid.uuid4()
    target = _row("bgp_rr", uuid, "NEW", "h1", full_hash="")
    _sweep(driver, [target], [_row("bgp_rr", uuid, "ACTIVE", "h1")])

    assert target.status == "ACTIVE"
    target.update.assert_called_once()


def test_an_active_that_answers_an_older_target_is_not_this_target_s_active(driver):
    """The agent reports ACTIVE for what it applied, which may be the previous
    value of a resource the control plane has since changed."""
    uuid = sys_uuid.uuid4()
    target = _row("evpn_port", uuid, "NEW", "h2")
    _sweep(driver, [target], [_row("evpn_port", uuid, "ACTIVE", "h1")])

    assert target.status == "NEW"


def test_a_failure_is_news_whatever_the_hashes_say(driver):
    uuid = sys_uuid.uuid4()
    target = _row("evpn_host", uuid, "ACTIVE", "h1")
    _sweep(driver, [target], [_row("evpn_host", uuid, "ERROR", "h2")])

    assert target.status == "ERROR"


def test_a_row_the_data_plane_never_reported_is_left_alone(driver):
    target = _row("evpn_nf", sys_uuid.uuid4(), "NEW", "h1")
    _sweep(driver, [target], [_row("evpn_nf", sys_uuid.uuid4(), "ACTIVE", "h1")])

    assert target.status == "NEW"
    target.update.assert_not_called()


def test_nothing_is_written_when_nothing_moved(driver):
    uuid = sys_uuid.uuid4()
    target = _row("evpn_nf", uuid, "ACTIVE", "h1", full_hash="f")
    _sweep(driver, [target], [_row("evpn_nf", uuid, "ACTIVE", "h1")])

    target.update.assert_not_called()


def test_every_kind_this_driver_writes_is_swept():
    """A kind added to the compiler and forgotten here is a row that goes back
    to being stuck at NEW."""
    from exordos_core.network.evpn.dm import models as evpn_models

    declared = {
        model.get_resource_kind()
        for model in (
            evpn_models.EvpnPort,
            evpn_models.EvpnHost,
            evpn_models.EvpnNf,
            evpn_models.BgpRr,
        )
    }
    assert set(evpn.OvsEvpnNetworkDriver.OWNED_KINDS) == declared
