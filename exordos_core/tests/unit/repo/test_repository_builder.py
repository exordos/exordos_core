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
import uuid

import pytest
from restalchemy.dm import filters as ra_filters

from exordos_core.repo.builders import repository
from exordos_core.repo.dm import models


@pytest.mark.parametrize("stale", [False, True])
def test_refresh_fetches_stale_elements_only_when_needed(stale):
    element = mock.Mock(
        version="1.0.0",
        uuid=uuid.uuid4(),
        installation_state=models.RepoElementInstallationState.UNINSTALLED,
        stable=False,
    )
    element.name = "example"
    instance = mock.Mock(uuid=uuid.uuid4(), refresh_rate=0)
    instance.iter_elements_in_inventory.return_value = [] if stale else [element]
    service = mock.Mock()

    with mock.patch.object(models.RepoElement, "objects") as objects:
        objects.get_all.side_effect = [[element], [element]]

        repository.RepoProxyBuilderService._refresh_repository(service, instance)

        expected_calls = [mock.call(filters={"repository": instance.uuid})]
        if stale:
            expected_calls.append(
                mock.call(filters={"uuid": ra_filters.In([element.uuid])})
            )
        assert objects.get_all.call_args_list == expected_calls

    if stale:
        element.delete.assert_called_once_with()
    else:
        element.delete.assert_not_called()
