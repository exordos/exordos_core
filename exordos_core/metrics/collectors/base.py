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

"""Platform state metrics.

State metrics describe the business objects of the platform (elements,
resources, nodes, ...) rather than the host they run on, in the spirit of
kube-state-metrics. Every collector turns a slice of the database into a
list of samples which are rendered in the Prometheus text exposition format.
"""

import abc
import dataclasses
import itertools
import typing as tp


@dataclasses.dataclass(frozen=True)
class Sample:
    name: str
    labels: dict[str, str]
    value: float


class AbstractCollector(abc.ABC):
    """Collects samples of one group of metrics."""

    @abc.abstractmethod
    def collect(self, session) -> tp.Iterable[Sample]:
        """Return the samples, reading the database through `session`."""


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_value(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return repr(float(value))


def render(samples: tp.Iterable[Sample]) -> str:
    """Render samples in the Prometheus text exposition format.

    The samples of a metric must be contiguous, so they are grouped by name
    keeping their order, and every metric is declared a gauge.
    """
    lines = []
    for name, group in itertools.groupby(
        sorted(samples, key=lambda s: s.name), key=lambda s: s.name
    ):
        lines.append(f"# TYPE {name} gauge")
        for sample in group:
            labels = ",".join(
                f'{k}="{_escape_label(str(v))}"' for k, v in sample.labels.items()
            )
            lines.append(f"{name}{{{labels}}} {_format_value(sample.value)}")
    return "\n".join(lines) + "\n" if lines else ""
