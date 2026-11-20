#    Copyright 2026 Genesis Corporation.
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.

import uuid

import pytest
from restalchemy.common import exceptions

from exordos_core.elements.dm.models import Element, Manifest
from exordos_core.repo.builders.element import InstalledManifest
from exordos_core.repo.dm.models import DatabaseDriverSpec, RepoElement, Repository


@pytest.mark.parametrize("model", [Manifest, Element, RepoElement, InstalledManifest])
def test_description_length(model):
    project_id = uuid.uuid4()
    kwargs = {"name": "example", "version": "1.0.0", "project_id": project_id}
    if model is RepoElement:
        kwargs["repository"] = Repository(
            name="example", project_id=project_id, driver_spec=DatabaseDriverSpec()
        )

    description = "т" * 65536
    assert model(**kwargs, description=description).description == description
    with pytest.raises(exceptions.TypeError):
        model(**kwargs, description=description + "т")
