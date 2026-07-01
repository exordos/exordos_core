#    Copyright 2025 Genesis Corporation.
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

from collections import defaultdict
import typing as tp

from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.infra.dm import models as sdk_models
from restalchemy.dm import filters as dm_filters
from restalchemy.dm import models as ra_models
from restalchemy.dm import properties
from restalchemy.dm import types

from exordos_core.user_api.network.dm import models


class TargetNodeSet(sdk_models.NodeSet):
    @classmethod
    def get_resource_kind(cls) -> str:
        """Return the resource kind."""
        return "target_node_set"


class IaasLB(models.LB, ua_models.InstanceWithDerivativesMixin):
    __derivative_model_map__ = {
        "target_node_set": TargetNodeSet,
    }

    @classmethod
    def get_resource_kind(cls) -> str:
        """Return the resource kind."""
        return "net_lb_iaas"

    def get_resource_target_fields(self) -> tp.Collection[str]:
        """Return the collection of target fields.

        Refer to the Resource model for more details about target fields.
        """
        return frozenset(
            (
                "uuid",
                "name",
                "type",
                "project_id",
            )
        )


class PaasLBAgent(
    ra_models.ModelWithUUID,
    ua_models.TargetResourceKindAwareMixin,
):
    status = properties.property(
        types.Enum([status.value for status in models.LBStatus]),
        default=models.LBStatus.NEW.value,
    )
    vhosts = properties.property(types.List())
    backend_pools = properties.property(types.Dict())

    @classmethod
    def get_resource_kind(cls) -> str:
        return "paas_lb_agent"

    def get_resource_target_fields(self) -> tp.Collection[str]:
        """Return the collection of target fields.

        Refer to the Resource model for more details about target fields.
        """
        return frozenset(
            (
                "uuid",
                "vhosts",
                "backend_pools",
            )
        )


class PaasLBNode(
    PaasLBAgent,
    ua_models.SchedulableToAgentFromAgentUUIDMixin,
):
    @classmethod
    def get_resource_kind(cls) -> str:
        return "paas_lb_node"


class PaasLB(IaasLB):
    __derivative_model_map__ = {
        "paas_lb_node": PaasLBNode,
        "paas_lb_agent": PaasLBAgent,
    }

    @classmethod
    def get_resource_kind(cls) -> str:
        """Return the resource kind."""
        return "net_lb_paas"

    def get_resource_target_fields(self) -> tp.Collection[str]:
        """Return the collection of target fields.

        Refer to the Resource model for more details about target fields.
        """
        return frozenset(
            (
                "uuid",
                "name",
                "type",
                "project_id",
            )
        )

    def get_vhosts(self):
        res = []

        vhosts = models.Vhost.objects.get_all(
            filters={
                "parent": dm_filters.EQ(self.uuid),
                "enabled": dm_filters.EQ(True),
            }
        )
        if not vhosts:
            return []

        routes_by_vhost = defaultdict(list)
        for route in models.Route.objects.get_all(
            filters={
                "parent": dm_filters.In([v.uuid for v in vhosts]),
                "enabled": dm_filters.EQ(True),
            }
        ):
            routes_by_vhost[route.parent.uuid].append(route)

        for vhost in vhosts:
            rvhost = {
                "uuid": str(vhost.uuid),
                "proto": vhost.protocol,
                "port": vhost.port,
                "domains": vhost.domains,
                "cert": (vhost.cert.dump_to_simple_view() if vhost.cert else None),
                "ext_sources": [
                    e.dump_to_simple_view() for e in vhost.external_sources
                ],
                "proxy_proto_from": (
                    str(vhost.proxy_protocol_from)
                    if vhost.proxy_protocol_from
                    else None
                ),
                "routes": {
                    str(r.uuid): {"cond": r.condition.dump_to_simple_view()}
                    for r in routes_by_vhost.get(vhost.uuid, [])
                },
            }

            res.append(rvhost)
        return res

    def get_backend_pools(self):
        return {
            str(b.uuid): {
                "endpoints": [e.dump_to_simple_view() for e in b.endpoints],
                "balance": b.balance,
            }
            for b in models.BackendPool.objects.get_all(filters={"parent": self.uuid})
        }
