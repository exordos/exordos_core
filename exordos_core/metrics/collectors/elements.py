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

import typing as tp

from exordos_core.elements.dm import models as em_models
from exordos_core.metrics.collectors import base

# Numeric codes of the EM statuses. Zero is deliberately unused so that a
# missing value is never mistaken for a status.
STATUS_CODES = {
    em_models.Status.NEW.value: 1,
    em_models.Status.IN_PROGRESS.value: 2,
    em_models.Status.ACTIVE.value: 3,
}

ELEMENT_STATUS = "exordos_em_element_status"
ELEMENT_INFO = "exordos_em_element_info"
RESOURCE_STATUS = "exordos_em_resource_status"


class ElementsCollector(base.AbstractCollector):
    """Status of every element, plus an info series carrying its metadata.

    The version lives in the info series only, so an upgrade does not split
    the status history of an element into several series.
    """

    def collect(self, session) -> tp.Iterable[base.Sample]:
        rows = session.execute(
            "SELECT uuid, name, version, install_type, project_id, status"
            f" FROM {em_models.Element.__tablename__}"
        ).fetchall()
        for row in rows:
            yield base.Sample(
                ELEMENT_STATUS,
                {"element": row["name"]},
                STATUS_CODES[row["status"]],
            )
            yield base.Sample(
                ELEMENT_INFO,
                {
                    "element": row["name"],
                    "element_uuid": str(row["uuid"]),
                    "version": row["version"],
                    "install_type": row["install_type"],
                    "project_id": str(row["project_id"] or ""),
                },
                1,
            )


class ResourcesCollector(base.AbstractCollector):
    """Status of every element resource.

    The kind is the manifest link prefix of the resource without the leading
    `$`, e.g. `core.compute.sets`.
    """

    def collect(self, session) -> tp.Iterable[base.Sample]:
        rows = session.execute(
            "SELECT r.uuid, r.name, r.status, r.resource_link_prefix,"
            " e.name AS element"
            f" FROM {em_models.Resource.__tablename__} r"
            f" JOIN {em_models.Element.__tablename__} e ON r.element = e.uuid"
        ).fetchall()
        for row in rows:
            yield base.Sample(
                RESOURCE_STATUS,
                {
                    "element": row["element"],
                    "kind": row["resource_link_prefix"].lstrip("$"),
                    "resource": row["name"],
                    "resource_uuid": str(row["uuid"]),
                },
                STATUS_CODES[row["status"]],
            )
