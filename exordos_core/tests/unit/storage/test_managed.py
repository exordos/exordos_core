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

from types import SimpleNamespace
from unittest import mock
import uuid

from gcl_sdk.agents.universal.drivers import pool
from restalchemy.api import constants as ra_c

from exordos_core.storage import managed
from exordos_core.storage.dm import models
from exordos_core.user_api.storage.api import controllers


def cluster(nodes=2, size=20):
    return models.StorageCluster(
        uuid=uuid.uuid4(),
        name="storage",
        nodes=nodes,
        size=size,
        driver_spec=pool.RawstorStorageClusterDriverSpec(endpoint="mds://core:7776/"),
    )


def test_zero_nodes_does_not_create_anything():
    with mock.patch.object(managed.vs.Variable, "objects") as objects:
        managed.reconcile(cluster(nodes=0))
    objects.get_one_or_none.assert_not_called()


def test_managed_vms_wait_for_storage_agent_and_reconcile_idempotently():
    target = cluster()
    vms, osts, agents = {}, {}, []

    def find(items, **kwargs):
        return items.get(kwargs["filters"]["uuid"].value)

    with (
        mock.patch.object(
            managed.vs.Variable,
            "objects",
            mock.Mock(
                get_one_or_none=mock.Mock(
                    return_value=SimpleNamespace(value="urn:images:ost")
                )
            ),
        ),
        mock.patch.object(
            managed.compute.Node,
            "objects",
            mock.Mock(get_one_or_none=lambda **kwargs: find(vms, **kwargs)),
        ),
        mock.patch.object(
            managed.compute.Node,
            "insert",
            new=lambda self: vms.setdefault(self.uuid, self),
        ),
        mock.patch.object(
            models.StorageNode,
            "objects",
            mock.Mock(get_one_or_none=lambda **kwargs: find(osts, **kwargs)),
        ),
        mock.patch.object(
            models.StorageNode,
            "insert",
            new=lambda self: osts.setdefault(self.uuid, self),
        ),
        mock.patch.object(
            managed.ua.UniversalAgent,
            "objects",
            mock.Mock(
                get_all=lambda **kwargs: [
                    a for a in agents if a.node == kwargs["filters"]["node"].value
                ]
            ),
        ),
    ):
        managed.reconcile(target)
        assert len(vms) == 2 and not osts
        for index, node in enumerate(vms.values()):
            disks = node.disk_spec.disks
            assert disks[0]["image"] == "urn:images:ost"
            assert disks[0]["size"] == 10
            assert disks[1]["size"] == 20
            assert all(d["tier"] == "WARM" and d["ephemeral"] for d in disks)
            node.default_network = {"ipv4": f"10.100.0.{20 + index}"}
            agents.append(
                SimpleNamespace(
                    uuid=uuid.uuid4(),
                    node=node.uuid,
                    status="ACTIVE",
                    list_capabilities=["guest_machine"],
                )
            )
        managed.reconcile(target)
        assert not osts
        for agent in agents:
            agent.list_capabilities.append("storage_node")
        managed.reconcile(target)
        managed.reconcile(target)
        assert len(vms) == len(osts) == 2
        assert len({n.driver_spec.failure_domain_path for n in osts.values()}) == 2
        for ost in osts.values():
            assert ost.driver_spec.location == "zfs://rawstor-ost/data"
            assert ost.driver_spec.endpoint == f"ost://{ost.driver_spec.bind_address}"
            assert ost.agent in {a.uuid for a in agents}


def test_managed_node_cleanup_uses_only_deterministic_vm_ids():
    target = cluster()
    nodes = {managed.vm_uuid(target, i): mock.Mock() for i in range(target.nodes)}
    with mock.patch.object(
        managed.compute.Node,
        "objects",
        mock.Mock(
            get_one_or_none=lambda **kwargs: nodes[kwargs["filters"]["uuid"].value]
        ),
    ):
        managed.delete_nodes(target)
    for node in nodes.values():
        node.delete.assert_called_once()


def test_managed_size_and_count_are_create_only_in_openapi():
    resource = controllers.StorageClustersController.__resource__
    schema = resource.generate_schema_object(ra_c.CREATE, "3.0.3")
    assert schema["properties"]["nodes"]["default"] == 0
    assert schema["properties"]["size"]["default"] == 10
    assert not schema["properties"]["nodes"].get("readOnly")
    assert not schema["properties"]["size"].get("readOnly")
