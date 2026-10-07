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

"""Realm repositories are registered from the bootstrap repository list."""

from unittest import mock

import pytest

from exordos_core.cmd import bootstrap
from exordos_core.repo.dm import models as repo_models


@pytest.mark.parametrize(
    "repo_url",
    [
        "http://10.40.0.1:8081/repo/",
        "http://10.40.0.1:8081/repo/project/exordos-elements/",
    ],
)
def test_bootstrap_registers_the_realm_repository_url_unchanged(repo_url):
    with mock.patch.object(bootstrap, "_ensure_repository") as ensure:
        bootstrap._ensure_repositories_from_spec({"repository": [repo_url]})

    ensure.assert_called_once()
    kwargs = ensure.call_args.kwargs
    assert kwargs["driver_spec"].url == repo_url
    assert kwargs["refresh_rate"] == 3600
    assert kwargs["sync_mode"] == repo_models.SyncMode.LAZY.value
