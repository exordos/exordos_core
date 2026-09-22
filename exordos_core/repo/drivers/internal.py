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

import json
import logging
import pathlib

from exordos_core.repo import internal
from exordos_core.repo.drivers import nginx

LOG = logging.getLogger(__name__)


class InternalProxyRepoDriver(nginx.NginxProxyRepoDriver):
    """A project's internal repository, served by the core LB.

    Elements and artifacts are read over HTTP like any nginx repository,
    but the repository-level index is built from the per-element
    inventories on the core node: a single index file rewritten by every
    push would lose entries when two pushes race.
    """

    SPEC_KINDS = ("internal",)

    def get_inventory(self) -> dict:
        root = pathlib.Path(
            internal.REPO_DIR,
            str(self._repository.project_id),
            internal.ELEMENTS_DIR,
        )
        elements: dict[str, dict] = {}
        # nginx stores an upload under a temporary name and renames it, and
        # the CLI writes an element's inventory last, so one that exists is
        # complete.
        for path in sorted(root.glob("*/*/inventory.json")):
            version = path.parent.name
            if version == "latest":
                continue
            try:
                inventory = json.loads(path.read_text())
            except (OSError, ValueError):
                LOG.warning("Skipping unreadable inventory %s", path)
                continue
            elements.setdefault(path.parent.parent.name, {})[version] = inventory
        return {"elements": elements}
