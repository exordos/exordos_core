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

import uuid as sys_uuid

import netaddr
import pytest
from gcl_sdk.infra.dm import models as sdk_infra_models
from restalchemy.common import contexts

from exordos_core.common import constants as c
from exordos_core.compute import constants as nc
from exordos_core.compute.dm import models as compute_models
from exordos_core.metrics import service
from exordos_core.metrics.collectors import base
from exordos_core.metrics.collectors import compute


def _by_name(samples, name):
    return [s for s in samples if s.name == name]


class BrokenSqlCollector(base.AbstractCollector):
    def collect(self, session):
        session.execute("SELECT * FROM no_such_table").fetchall()
        return []


@pytest.fixture
def pool(user_api, pool_factory):
    view = pool_factory(status="ACTIVE")
    compute_models.MachinePool.restore_from_simple_view(**view).insert()
    return view


@pytest.fixture
def subnet(user_api):
    network = compute_models.Network(
        driver_spec={"driver": "dummy"}, project_id=c.ZERO_UUID
    )
    network.insert()
    subnet = compute_models.Subnet(
        network=network.uuid,
        cidr=netaddr.IPNetwork("10.0.0.0/24"),
        project_id=c.ZERO_UUID,
    )
    subnet.insert()
    return subnet


class TestComputeCollectors:
    def test_nodes_and_sets(self, pool, subnet) -> None:
        node_set = compute_models.NodeSet(
            name="workers",
            description="Workers",
            project_id=c.ZERO_UUID,
            cores=2,
            ram=2048,
            replicas=1,
            status=nc.NodeStatus.ACTIVE.value,
            disk_spec=sdk_infra_models.SetDisksSpec(
                disks=[
                    {"size": 10, "image": "base.raw"},
                    {"size": 100, "label": "data"},
                ]
            ),
        )
        node_set.insert()
        # A node of a set gets no hostname, the machine takes its name
        set_node = compute_models.Node(
            name="workers-0",
            project_id=c.ZERO_UUID,
            cores=2,
            ram=2048,
            node_set=node_set.uuid,
            status=nc.NodeStatus.ACTIVE.value,
            disk_spec=sdk_infra_models.RootDiskSpec(image="base.raw", size=10),
        )
        set_node.insert()
        node = compute_models.Node(
            name="db",
            hostname="db-host",
            project_id=c.ZERO_UUID,
            cores=1,
            ram=1024,
            status=nc.NodeStatus.ERROR.value,
            disk_spec=sdk_infra_models.RootDiskSpec(image="db.raw", size=20),
        )
        node.insert()
        compute_models.Machine(
            name="db",
            project_id=c.ZERO_UUID,
            cores=1,
            ram=1024,
            node=node.uuid,
            pool=sys_uuid.UUID(pool["uuid"]),
        ).insert()
        for ip in ("10.0.0.20", "10.0.0.10"):
            compute_models.Port(
                name="port",
                project_id=c.ZERO_UUID,
                subnet=subnet.uuid,
                node=node.uuid,
                ipv4=netaddr.IPAddress(ip),
            ).insert()

        with contexts.Context().session_manager() as session:
            node_samples = list(compute.NodesCollector().collect(session))
            set_samples = list(compute.SetsCollector().collect(session))

        infos = {
            s.labels["node"]: s.labels
            for s in _by_name(node_samples, compute.NODE_INFO)
        }
        assert infos["db"] == {
            "node_uuid": str(node.uuid),
            "node": "db",
            "hostname": "db-host",
            "description": "",
            "project_id": str(c.ZERO_UUID),
            "node_type": "VM",
            "set": "",
            "set_uuid": "",
            "element": "",
            "image": "db.raw",
            "disks": "20G",
            "ipv4": "10.0.0.10, 10.0.0.20",
            "pool": pool["name"],
        }
        assert infos["workers-0"]["hostname"] == "workers-0"
        assert infos["workers-0"]["set"] == "workers"
        assert infos["workers-0"]["set_uuid"] == str(node_set.uuid)
        assert infos["workers-0"]["ipv4"] == ""
        assert infos["workers-0"]["pool"] == ""

        statuses = {
            s.labels["node_uuid"]: s.value
            for s in _by_name(node_samples, compute.NODE_STATUS)
        }
        assert statuses == {str(node.uuid): 6, str(set_node.uuid): 5}
        (created,) = [
            s
            for s in _by_name(node_samples, compute.NODE_CREATED)
            if s.labels["node_uuid"] == str(node.uuid)
        ]
        assert abs(created.value - node.created_at.timestamp()) < 1

        (set_info,) = _by_name(set_samples, compute.SET_INFO)
        assert set_info.labels["disks"] == "10G, 100G"
        assert set_info.labels["image"] == "base.raw"
        (set_disk,) = _by_name(set_samples, compute.SET_DISK)
        assert set_disk.value == 110 * compute.GIB

    def test_pools(self, pool) -> None:
        with contexts.Context().session_manager() as session:
            samples = list(compute.PoolsCollector().collect(session))

        values = {s.name: s.value for s in samples}
        assert values == {
            compute.POOL_STATUS: 1,
            compute.POOL_CORES: pool["all_cores"],
            compute.POOL_CORES_AVAILABLE: pool["avail_cores"],
            compute.POOL_RAM: pool["all_ram"] * compute.MIB,
            compute.POOL_RAM_AVAILABLE: pool["avail_ram"] * compute.MIB,
        }


class TestStateMetricsService:
    def test_broken_query_does_not_stop_others(self, pool, tmp_path) -> None:
        path = tmp_path / "core_state.prom"
        svc = service.StateMetricsService(
            textfile_path=str(path),
            collectors=[BrokenSqlCollector(), compute.PoolsCollector()],
        )

        svc._iteration()

        assert compute.POOL_STATUS in path.read_text()
