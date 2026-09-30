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

import contextlib
import uuid as sys_uuid
from unittest import mock

from exordos_core.metrics.collectors import base
from exordos_core.metrics import service
from exordos_core.metrics.collectors import compute
from exordos_core.metrics.collectors import elements


class FakeSession:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, query):
        result = mock.Mock()
        result.fetchall.return_value = self._rows
        return result


class FailingCollector(base.AbstractCollector):
    def collect(self, session):
        raise RuntimeError("boom")


class StaticCollector(base.AbstractCollector):
    def __init__(self, samples):
        self._samples = samples

    def collect(self, session):
        return self._samples


def _patch_context(monkeypatch):
    context = mock.Mock()
    context.session_manager.return_value = contextlib.nullcontext(object())
    monkeypatch.setattr(service.contexts, "Context", lambda: context)
    monkeypatch.setattr(
        service.sql_utils, "savepoint", lambda: contextlib.nullcontext(object())
    )


class TestRender:
    def test_empty(self):
        assert base.render([]) == ""

    def test_escapes_label_values(self):
        sample = base.Sample("m", {"a": 'x"y\\z\nw'}, 1)

        assert base.render([sample]) == ('# TYPE m gauge\nm{a="x\\"y\\\\z\\nw"} 1\n')

    def test_float_value(self):
        assert base.render([base.Sample("m", {}, 0.5)]) == ("# TYPE m gauge\nm{} 0.5\n")

    def test_groups_samples_by_name(self):
        samples = [
            base.Sample("b", {"x": "1"}, 1),
            base.Sample("a", {"x": "1"}, 2),
            base.Sample("b", {"x": "2"}, 3),
        ]

        assert base.render(samples) == (
            '# TYPE a gauge\na{x="1"} 2\n# TYPE b gauge\nb{x="1"} 1\nb{x="2"} 3\n'
        )


class TestElementsCollector:
    def test_status_and_info(self):
        element_uuid = sys_uuid.uuid4()
        session = FakeSession(
            [
                {
                    "uuid": element_uuid,
                    "name": "foo",
                    "version": "1.2.3",
                    "install_type": "MANUAL",
                    "project_id": None,
                    "status": "IN_PROGRESS",
                },
            ]
        )

        samples = list(elements.ElementsCollector().collect(session))

        assert samples == [
            base.Sample(elements.ELEMENT_STATUS, {"element": "foo"}, 2),
            base.Sample(
                elements.ELEMENT_INFO,
                {
                    "element": "foo",
                    "element_uuid": str(element_uuid),
                    "version": "1.2.3",
                    "install_type": "MANUAL",
                    "project_id": "",
                },
                1,
            ),
        ]

    def test_status_codes(self):
        assert elements.STATUS_CODES == {"NEW": 1, "IN_PROGRESS": 2, "ACTIVE": 3}


class TestResourcesCollector:
    def test_status(self):
        resource_uuid = sys_uuid.uuid4()
        session = FakeSession(
            [
                {
                    "uuid": resource_uuid,
                    "name": "grafana",
                    "status": "ACTIVE",
                    "resource_link_prefix": "$grafanaaas.types.grafana.instances",
                    "element": "observability",
                },
            ]
        )

        samples = list(elements.ResourcesCollector().collect(session))

        assert samples == [
            base.Sample(
                elements.RESOURCE_STATUS,
                {
                    "element": "observability",
                    "kind": "grafanaaas.types.grafana.instances",
                    "resource": "grafana",
                    "resource_uuid": str(resource_uuid),
                },
                3,
            ),
        ]


class TestStateMetricsService:
    def test_writes_rendered_samples(self, monkeypatch, tmp_path):
        _patch_context(monkeypatch)
        path = tmp_path / "core_state.prom"
        svc = service.StateMetricsService(
            textfile_path=str(path),
            collectors=[StaticCollector([base.Sample("m", {"a": "b"}, 3)])],
        )

        svc._iteration()

        assert path.read_text() == '# TYPE m gauge\nm{a="b"} 3\n'
        assert [p.name for p in tmp_path.iterdir()] == ["core_state.prom"]

    def test_broken_collector_does_not_stop_others(self, monkeypatch, tmp_path):
        _patch_context(monkeypatch)
        path = tmp_path / "core_state.prom"
        svc = service.StateMetricsService(
            textfile_path=str(path),
            collectors=[
                FailingCollector(),
                StaticCollector([base.Sample("m", {}, 1)]),
            ],
        )

        svc._iteration()

        assert path.read_text() == "# TYPE m gauge\nm{} 1\n"

    def test_write_failure_does_not_raise(self, monkeypatch, tmp_path):
        _patch_context(monkeypatch)
        warning = mock.Mock()
        monkeypatch.setattr(service.LOG, "warning", warning)
        svc = service.StateMetricsService(
            textfile_path=str(tmp_path / "missing" / "core_state.prom"),
            collectors=[StaticCollector([base.Sample("m", {}, 1)])],
        )

        svc._iteration()
        svc._iteration()

        # Repeated failures are reported once per FAILURE_LOG_PERIOD
        warning.assert_called_once()

    def test_nothing_collected_empties_file(self, monkeypatch, tmp_path):
        _patch_context(monkeypatch)
        path = tmp_path / "core_state.prom"
        path.write_text("m{} 1\n")
        svc = service.StateMetricsService(
            textfile_path=str(path), collectors=[StaticCollector([])]
        )

        svc._iteration()

        # Series of the objects that are gone become stale, not frozen
        assert path.read_text() == ""


class TestComputeDiskSpec:
    def test_root_disk(self):
        spec = {"kind": "root_disk", "image": "base.raw", "size": 15}

        assert compute._disk_sizes(spec) == [15]
        assert compute._image(spec) == "base.raw"

    def test_disks(self):
        spec = {
            "kind": "disks",
            "disks": [{"size": 10, "image": "base.raw"}, {"size": 100}],
        }

        assert compute._disk_sizes(spec) == [10, 100]
        assert compute._image(spec) == "base.raw"
        assert compute._format_disks([10, 100]) == "10G, 100G"

    def test_diskless(self):
        spec = {"kind": "disks", "disks": []}

        assert compute._disk_sizes(spec) == []
        assert compute._image(spec) == ""
        assert compute._disk_sizes(None) == []
        assert compute._image(None) == ""
