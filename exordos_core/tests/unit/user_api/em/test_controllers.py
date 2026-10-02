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

from unittest import mock

import pytest
from restalchemy.common import contexts

from exordos_core.elements.dm import models
from exordos_core.user_api.em.api import controllers


@pytest.mark.parametrize(
    "controller_cls, loads_elements",
    [
        (controllers.ElementResourceController, True),
        (controllers.ResourceAllController, True),
        (controllers.ElementImportController, False),
        (controllers.ImportAllController, False),
        (controllers.ElementExportController, False),
        (controllers.ExportAllController, False),
    ],
)
def test_read_controllers_do_not_reload_the_whole_engine(
    monkeypatch, controller_cls, loads_elements
):
    load_from_database = mock.Mock()
    load_elements = mock.Mock()
    monkeypatch.setattr(models.element_engine, "load_from_database", load_from_database)
    monkeypatch.setattr(models.element_engine, "load_elements", load_elements)

    context = contexts.ContextWithStorage()
    context.iam_context = mock.MagicMock()
    with context.context_manager():
        controller_cls(request=mock.MagicMock())

    load_from_database.assert_not_called()
    assert load_elements.called is loads_elements
