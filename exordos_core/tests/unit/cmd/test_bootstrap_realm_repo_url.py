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

"""The realm spec's `repo_url` becomes the `realm_repo_url` variable."""

from unittest import mock

import pytest

from exordos_core.bootstrap import defaults
from exordos_core.common import constants as c


def test_without_repo_url_there_is_nothing_to_set():
    with mock.patch.object(defaults, "set_var") as set_var:
        assert defaults.set_realm_repo_url_var({"realm_uuid": "x"}) is True

    set_var.assert_not_called()


@pytest.mark.parametrize(
    "repo_url",
    ["http://10.40.0.1:8081/repo/", "http://10.40.0.1:8081/repo"],
)
def test_repo_url_is_set_with_a_trailing_slash(repo_url):
    with mock.patch.object(defaults, "set_var", return_value=True) as set_var:
        assert defaults.set_realm_repo_url_var({"repo_url": repo_url}) is True

    set_var.assert_called_once_with(
        "realm_repo_url", "http://10.40.0.1:8081/repo/", c.VAR_REALM_REPO_URL_UUID
    )


def test_waits_until_the_variable_exists():
    # set_var answers False while the manifest hasn't declared the variable;
    # bootstrap retries the task until it is there.
    with mock.patch.object(defaults, "set_var", return_value=False):
        assert defaults.set_realm_repo_url_var({"repo_url": "http://x/repo/"}) is False
