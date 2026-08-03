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

"""The border target resource is declared twice, like the evpn ones — here as
what the control plane schedules, and in gcl_sdk as what the agent renders.
It had no test saying so, and it is what the border cost: a `routes` the data
plane dropped and the control plane kept sending failed *every* border
resource, with a traceback that named neither the field nor the border.

The agent now skips a name it does not have rather than dying on it, which
turns that from an outage into a resource that never settles. This is what
keeps it from being either.

Lives with the functional suite because it imports the data-plane half, as
the evpn contract test does — the unit environment resolves gcl_sdk from the
index, where a published release can be older than both halves here.
"""

import pytest

from exordos_core.network.border.dm import models as cp_models

# The data-plane model ships in gcl_sdk, which this installation pins to a
# published release. Skip with the remedy in the message rather than fail at
# collection; an editable checkout of gcl_sdk runs this in full.
dp_models = pytest.importorskip(
    "gcl_sdk.paas.dm.border",
    reason=(
        "needs a gcl_sdk that carries the border data-plane model "
        "(install gcl_sdk from a checkout to run this)"
    ),
)


def _fields(model):
    # `agent_uuid` is the CP's scheduling handle (which agent gets this
    # resource), not part of the value the agent renders — the one field the
    # two halves are meant to differ by.
    return set(model.properties.properties.keys()) - {"uuid", "agent_uuid"}


@pytest.mark.parametrize("cp", [cp_models.BorderAgent, cp_models.BorderNode])
def test_the_control_plane_names_nothing_the_data_plane_lacks(cp):
    """One data-plane model serves both kinds: what differs between them is
    which agent the resource is scheduled to, not what it says.

    Deliberately one-directional. Equality would be the stronger statement,
    but it cannot hold while a field is being retired: dropping it from the
    control plane has to ship *before* the release that drops it from the
    data plane, or an installation that rebuilds picks up a data plane
    missing a field the core still sends. `routes` is in exactly that
    window, and asserting equality here would make the release that has to
    come first the one that cannot.

    The direction that is asserted is the one that breaks things. The other
    is a field the agent has and is never given, which costs it its default.
    """
    assert _fields(cp) <= _fields(dp_models.Border)


@pytest.mark.parametrize("cp", [cp_models.BorderAgent, cp_models.BorderNode])
def test_target_fields_are_all_declared(cp):
    """What the CP hashes into the target value has to exist on both sides.

    A name only the CP has is worse than a value that does not arrive: it is
    in the target hash the agent cannot reproduce, so the resource is applied
    and re-applied for ever without ever matching.
    """
    assert set(cp().get_resource_target_fields()) <= _fields(dp_models.Border) | {
        "uuid"
    }
