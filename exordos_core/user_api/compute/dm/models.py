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

import uuid

from restalchemy.dm import filters
from restalchemy.dm import relationships
from restalchemy.dm import types
from restalchemy.storage import exceptions

from exordos_core.common.dm import models as common_models
from exordos_core.common.exceptions import ValidateException
from exordos_core.compute.dm import models


class Node(common_models.CustomPropertiesMixin, models.Node):
    __custom_properties__ = {
        "ports": types.TypedList(types.UUID()),
        "bootstrap": types.Dict(),
    }
    node_set = relationships.relationship(models.NodeSet, read_only=True)

    def __init__(self, ports=(), bootstrap=None, **kwargs):
        super().__init__(**kwargs)
        self._check_custom_property_value("ports", list(ports))
        self.ports = list(ports)
        self.bootstrap = bootstrap or {}
        if self.bootstrap and (
            set(self.bootstrap) != {"description", "path", "content", "command"}
            or any(not isinstance(v, str) for v in self.bootstrap.values())
        ):
            raise ValidateException(err="Invalid bootstrap configuration")

    def insert(self, session=None):
        from exordos_core.network.contracts import atomic_session
        from exordos_core.network.routed import requested_network

        with atomic_session(self._get_engine(), session) as current:
            attached = []
            if self.ports:
                if len(set(self.ports)) != len(self.ports):
                    raise ValidateException(err="Ports must be distinct")
                network = requested_network(
                    self.default_network, self.project_id, current
                )
                if network is None:
                    raise ValidateException(
                        err="Existing ports require a logical network"
                    )
                for index, identity in enumerate(self.ports):
                    port = models.Port.objects.get_one(
                        filters={
                            "uuid": filters.EQ(identity),
                            "project_id": filters.EQ(self.project_id),
                        },
                        session=current,
                        locked=True,
                    )
                    subnet = models.Subnet.objects.get_one(
                        filters={"uuid": filters.EQ(port.subnet)}, session=current
                    )
                    local_network = models.Network.objects.get_one(
                        filters={"uuid": filters.EQ(subnet.network)}, session=current
                    )
                    if port.node is not None or (
                        index == 0
                        and str(local_network.owner_network or local_network.uuid)
                        != str(network.uuid)
                    ):
                        raise exceptions.ConflictRecords(
                            model="Port",
                            msg="port is attached or belongs to another network",
                        )
                    attached.append(port)
            result = super().insert(session=current)
            for index, port in enumerate(attached):
                port.node, port.owner_node, port.index = self.uuid, self.uuid, index
                port.update(session=current)
            if self.bootstrap:
                from exordos_core.config.dm.models import Config

                Config.restore_from_simple_view(
                    uuid=str(uuid.uuid5(self.uuid, "provider-bootstrap")),
                    project_id=str(self.project_id),
                    name="provider-bootstrap",
                    description=self.bootstrap["description"],
                    path=self.bootstrap["path"],
                    mode="0600",
                    target={"kind": "node", "node": str(self.uuid)},
                    body={"kind": "text", "content": self.bootstrap["content"]},
                    on_change={"kind": "shell", "command": self.bootstrap["command"]},
                ).insert(session=current)
            return result

    def delete(self, session=None, authorize_bootstrap=None):
        from exordos_core.config.dm.models import Config
        from exordos_core.network.contracts import atomic_session

        with atomic_session(self._get_engine(), session) as current:
            bootstrap = Config.objects.get_one_or_none(
                filters={
                    "uuid": filters.EQ(uuid.uuid5(self.uuid, "provider-bootstrap")),
                    "project_id": filters.EQ(self.project_id),
                },
                session=current,
                locked=True,
            )
            if (
                bootstrap is not None
                and bootstrap.target.KIND == "node"
                and bootstrap.target.node == self.uuid
            ):
                if authorize_bootstrap is not None:
                    authorize_bootstrap()
                bootstrap.delete(session=current, owner_node=self.uuid)
            return super().delete(session=current)


class Volume(models.Volume, common_models.CastToBaseMixin):
    __cast_fields__ = ("node",)

    node = relationships.relationship(Node, prefetch=True)
