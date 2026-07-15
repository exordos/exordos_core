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

from gcl_sdk.agents.universal.dm import models as ua_models
from restalchemy.dm import models as ra_models
from restalchemy.dm import properties
from restalchemy.dm import types

from exordos_core.network.lb.dm import models as lb_models
from exordos_core.user_api.network.dm import models


class BorderAgent(
    ra_models.ModelWithUUID,
    ua_models.TargetResourceKindAwareMixin,
):
    """Border capability applied on the core node's agent (step 0).

    Carries the NAT / forward rules the DP agent renders into nftables.
    """

    status = properties.property(
        types.Enum([status.value for status in models.LBStatus]),
        default=models.LBStatus.NEW.value,
    )
    snat_rules = properties.property(types.List(), default=lambda: [])
    forwards = properties.property(types.List(), default=lambda: [])
    routes = properties.property(types.List(), default=lambda: [])

    @classmethod
    def get_resource_kind(cls) -> str:
        return "border_agent"

    def get_resource_target_fields(self):
        return frozenset(
            (
                "uuid",
                "snat_rules",
                "forwards",
                "routes",
            )
        )


class BorderNode(
    BorderAgent,
    ua_models.SchedulableToAgentFromAgentUUIDMixin,
):
    """Same capability, advertised by hypervisor agents (distributed egress)."""

    @classmethod
    def get_resource_kind(cls) -> str:
        return "border_node"


class IaasBorder(models.Border, ua_models.InstanceWithDerivativesMixin):
    """IaaS view of a `core` (dedicated VM) border.

    Mirrors IaasLB: for type.kind == "core" the iaas builder materializes a
    single-replica target_node_set (the gateway VM); the paas builder then
    schedules the border_node capability to that VM's agent.
    """

    __derivative_model_map__ = {
        "target_node_set": lb_models.TargetNodeSet,
    }

    @classmethod
    def get_resource_kind(cls) -> str:
        return "net_border_iaas"

    def get_resource_target_fields(self):
        return frozenset(
            (
                "uuid",
                "name",
                "type",
                "project_id",
            )
        )


class PaasBorder(models.Border, ua_models.InstanceWithDerivativesMixin):
    __derivative_model_map__ = {
        "border_agent": BorderAgent,
        "border_node": BorderNode,
    }

    @classmethod
    def get_resource_kind(cls) -> str:
        return "net_border_paas"

    def get_resource_target_fields(self):
        return frozenset(
            (
                "uuid",
                "name",
                "project_id",
                "node",
                "type",
            )
        )

    def get_snat_rules(self):
        # Rules are carried inline on the Border resource.
        return self.snat_rules or []

    def get_forwards(self):
        return self.forwards or []

    def get_routes(self):
        # Reserved for explicit static routes the border must install.
        return []
