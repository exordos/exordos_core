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

"""The evpn target resources are the data plane's models (gcl_sdk) with
the control plane's scheduling added on top. A field the control plane
declared on its own would be a value that silently never arrives, so the
two must still name the same fields.
"""

import pytest

from gcl_sdk.paas.dm import evpn as dp_models

from exordos_core.network.evpn.dm import models as cp_models

MODELS = (
    (cp_models.EvpnPort, dp_models.EvpnPort),
    (cp_models.EvpnHost, dp_models.EvpnHost),
    (cp_models.EvpnNf, dp_models.EvpnNf),
    (cp_models.BgpRr, dp_models.BgpRr),
)


def _fields(model):
    # `agent_uuid` is the CP's scheduling handle (which agent gets this
    # resource), not part of the value the agent renders — the one field the
    # two halves are meant to differ by.
    return set(model.properties.properties.keys()) - {"uuid", "agent_uuid"}


@pytest.mark.parametrize("cp, dp", MODELS)
def test_both_halves_of_the_contract_name_the_same_fields(cp, dp):
    assert _fields(cp) == _fields(dp)
