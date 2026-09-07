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

from restalchemy.common import contexts

from exordos_core.compute.dm import models as compute_models
from exordos_core.telemetry import service


class TestCollectComputeNodes:
    def test_aggregates_match_row_sums(self, default_node) -> None:
        data = {}
        with contexts.Context().session_manager():
            service.TelemetryService._collect_compute_nodes(data)
            nodes = compute_models.Node.objects.get_all()

        assert data["nodes_count"] == len(nodes)
        assert data["nodes_total_cores"] == sum(n.cores for n in nodes)
        assert data["nodes_total_ram"] == sum(n.ram for n in nodes)

    def test_aggregates_on_empty_table(self, user_api) -> None:
        data = {}
        with contexts.Context().session_manager():
            service.TelemetryService._collect_compute_nodes(data)

        # COALESCE keeps the sums numeric when there are no rows at all.
        assert data == {
            "nodes_count": 0,
            "nodes_total_cores": 0,
            "nodes_total_ram": 0,
        }
