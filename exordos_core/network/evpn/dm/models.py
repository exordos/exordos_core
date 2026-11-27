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

"""The control plane's half of the evpn target resources.

Fields are declared once, in `gcl_sdk.paas.dm.evpn`; this adds the resource
kind, the hashed fields and the target agent.
"""

import typing as tp

from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.paas.dm import evpn as dp_models


class _TargetFieldsMixin:
    """Hash every field the data plane declares.

    Less `status`, which the agent reports, and `agent_uuid`, which only
    says where the resource goes.
    """

    def get_resource_target_fields(self) -> tp.Collection[str]:
        return frozenset(self.properties.properties) - {"status", "agent_uuid"}


class EvpnPort(
    _TargetFieldsMixin,
    dp_models.EvpnPort,
    ua_models.TargetResourceKindAwareMixin,
    ua_models.SchedulableToAgentFromAgentUUIDMixin,
):
    """Per-port slice of the ovs_evpn data plane.

    Scheduled to the universal agent of the port's hypervisor node.
    """

    @classmethod
    def get_resource_kind(cls) -> str:
        return "evpn_port"


class EvpnHost(
    _TargetFieldsMixin,
    dp_models.EvpnHost,
    ua_models.TargetResourceKindAwareMixin,
    ua_models.SchedulableToAgentFromAgentUUIDMixin,
):
    """Per-hypervisor EVPN stack parameters; uuid == node uuid."""

    @classmethod
    def get_resource_kind(cls) -> str:
        return "evpn_host"


class EvpnNf(
    _TargetFieldsMixin,
    dp_models.EvpnNf,
    ua_models.TargetResourceKindAwareMixin,
    ua_models.SchedulableToAgentFromAgentUUIDMixin,
):
    """One network function, compiled for one host.

    uuid == uuid5(nf, node), so a function shared by several hosts has one
    slice on each and they converge independently.
    """

    @classmethod
    def get_resource_kind(cls) -> str:
        return "evpn_nf"


class BgpRr(
    _TargetFieldsMixin,
    dp_models.BgpRr,
    ua_models.TargetResourceKindAwareMixin,
    ua_models.SchedulableToAgentFromAgentUUIDMixin,
):
    """Route reflector parameters; uuid == the RR node's agent uuid."""

    @classmethod
    def get_resource_kind(cls) -> str:
        return "bgp_rr"
