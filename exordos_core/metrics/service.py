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

import logging
import os
import time

from gcl_looper.services import basic
from restalchemy.common import contexts
from restalchemy.storage.sql import utils as sql_utils

from exordos_core.metrics.collectors import base
from exordos_core.metrics import opts
from exordos_core.metrics.collectors import compute
from exordos_core.metrics.collectors import elements

LOG = logging.getLogger(__name__)

# The write fails on every iteration while the textfile directory is missing
# (e.g. a base image without it), so report it at most this often.
FAILURE_LOG_PERIOD = 10 * 60


def default_collectors() -> list[base.AbstractCollector]:
    return [
        elements.ElementsCollector(),
        elements.ResourcesCollector(),
        compute.NodesCollector(),
        compute.SetsCollector(),
        compute.PoolsCollector(),
    ]


class StateMetricsService(basic.BasicService):
    """Periodically writes platform state metrics for node_exporter.

    Unlike TelemetryService, which sends aggregates to the ecosystem, these
    metrics stay inside the installation and describe every object. The
    textfile collector of node_exporter serves the file, so a series which
    is gone from it becomes stale at the next scrape. If the service stops,
    the file stays with the last values; `node_textfile_mtime_seconds` shows
    how old they are.
    """

    def __init__(
        self, textfile_path=opts.DEFAULT_TEXTFILE_PATH, collectors=None, **kwargs
    ):
        super().__init__(**kwargs)
        self._textfile_path = textfile_path
        self._collectors = (
            collectors if collectors is not None else default_collectors()
        )
        self._last_failure_log = None

    def _collect(self) -> list[base.Sample]:
        samples = []
        with contexts.Context().session_manager():
            for collector in self._collectors:
                # A failed query aborts the transaction, the savepoint keeps
                # it usable for the next collectors.
                try:
                    with sql_utils.savepoint() as session:
                        samples.extend(collector.collect(session))
                except Exception:
                    LOG.exception(
                        "Failed to collect %s metrics", type(collector).__name__
                    )
        return samples

    def _write(self, payload: str) -> None:
        # node_exporter reads *.prom only, and the rename is atomic, so a
        # scrape never sees a half written file.
        tmp_path = f"{self._textfile_path}.tmp"
        try:
            with open(tmp_path, "w") as f:
                f.write(payload)
            os.replace(tmp_path, self._textfile_path)
        except OSError as e:
            now = time.monotonic()
            if (
                self._last_failure_log is None
                or now - self._last_failure_log >= FAILURE_LOG_PERIOD
            ):
                self._last_failure_log = now
                LOG.warning(
                    "Failed to write state metrics to %s: %s",
                    self._textfile_path,
                    e,
                )
            return
        self._last_failure_log = None

    def _iteration(self):
        self._write(base.render(self._collect()))
