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
import uuid as sys_uuid

import pytest

from exordos_core.bootstrap import defaults
from exordos_core.common import constants as c


@pytest.mark.parametrize("spec", [{}, {"realm_domain": None}, {"realm_domain": ""}])
def test_set_realm_domain_var_without_domain_skips_database(spec):
    with (
        mock.patch.object(defaults.vs_models, "Variable") as variable,
        mock.patch.object(defaults.vs_models, "Value") as value,
    ):
        value.objects.get_one_or_none.return_value = None
        variable.objects.get_one_or_none.return_value = None

        assert defaults.set_realm_domain_var(spec) is True

        value.objects.get_one_or_none.assert_not_called()
        variable.objects.get_one_or_none.assert_not_called()
        value.assert_not_called()


def test_set_realm_domain_var_with_domain_waits_for_variable():
    with (
        mock.patch.object(defaults.vs_models, "Variable") as variable,
        mock.patch.object(defaults.vs_models, "Value") as value,
    ):
        value.objects.get_one_or_none.return_value = None
        variable.objects.get_one_or_none.return_value = None

        assert defaults.set_realm_domain_var({"realm_domain": "realm.example"}) is False

        value.assert_not_called()


def test_set_realm_domain_var_with_domain_inserts_value():
    with (
        mock.patch.object(defaults.vs_models, "Variable") as variable,
        mock.patch.object(defaults.vs_models, "Value") as value,
    ):
        value.objects.get_one_or_none.return_value = None

        assert defaults.set_realm_domain_var({"realm_domain": "realm.example"}) is True

        value.assert_called_once_with(
            uuid=sys_uuid.UUID("a6c149dc-9cfe-49c0-a0f0-d04e156b564e"),
            variable=variable.objects.get_one_or_none.return_value,
            value="realm.example",
            project_id=c.EM_HIDDEN_PROJECT_ID,
        )
        value.return_value.insert.assert_called_once_with()


def test_set_realm_domain_var_with_existing_value_is_idempotent():
    with (
        mock.patch.object(defaults.vs_models, "Variable") as variable,
        mock.patch.object(defaults.vs_models, "Value") as value,
    ):
        assert defaults.set_realm_domain_var({"realm_domain": "realm.example"}) is True

        variable.objects.get_one_or_none.assert_not_called()
        value.assert_not_called()
