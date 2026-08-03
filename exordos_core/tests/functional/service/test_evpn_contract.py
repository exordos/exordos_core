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

"""The evpn target resources are declared twice — once here, as what the
control plane computes and schedules, and once in gcl_sdk, as what the agent
renders. The two halves must name the same fields: a field added on one side
alone is not a compile error anywhere, it is a value that silently never
arrives (or one the agent ignores).

Lives with the functional suite because it imports the data-plane half, as
the e2e test does — the unit environment resolves gcl_sdk from the index,
where the SDN data plane is not released yet.
"""

import pytest

from exordos_core.network.evpn.dm import models as cp_models

# The data-plane models ships in gcl_sdk, which this installation pins to a published
# release. Until one carries the SDN work a clean environment cannot import
# it at all — and a collection error reads as a broken suite rather than as a
# missing dependency. Skip with the remedy in the message; an editable
# checkout of gcl_sdk runs these in full.
dp_models = pytest.importorskip(
    "gcl_sdk.paas.dm.evpn",
    reason=(
        "needs a gcl_sdk that carries the evpn data-plane models "
        "(unreleased; install gcl_sdk from a checkout to run this)"
    ),
)


def _fields(model):
    # `agent_uuid` is the CP's scheduling handle (which agent gets this
    # resource), not part of the value the agent renders — the one field the
    # two halves are meant to differ by.
    return set(model.properties.properties.keys()) - {"uuid", "agent_uuid"}


@pytest.mark.parametrize(
    "cp, dp",
    [
        (cp_models.EvpnPort, dp_models.EvpnPort),
        (cp_models.EvpnHost, dp_models.EvpnHost),
        (cp_models.BgpRr, dp_models.BgpRr),
    ],
)
def test_both_halves_of_the_contract_name_the_same_fields(cp, dp):
    assert _fields(cp) == _fields(dp)


def test_target_fields_are_all_declared():
    """What the CP hashes into the target value has to exist on both sides,
    or the agent is handed a key it cannot read."""
    for model in (cp_models.EvpnPort, cp_models.EvpnHost, cp_models.BgpRr):
        assert set(model().get_resource_target_fields()) <= _fields(model) | {"uuid"}
