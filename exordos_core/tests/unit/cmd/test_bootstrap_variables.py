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

from exordos_core.bootstrap import defaults
from exordos_core.cmd import bootstrap
from exordos_core.vs.dm import models


VAR_UUID = uuid.UUID("8b8a44fc-40b7-4ade-bfd2-20b5c02a9b61")


@pytest.mark.parametrize("spec", [{}, {"variables": {}}])
def test_no_variables(spec):
    with mock.patch.object(defaults, "set_var") as set_var:
        bootstrap._set_variables_from_spec(spec)
    set_var.assert_not_called()


@pytest.mark.parametrize("value", ["hello", 0, 1.5, False, None, [], {"a": [1]}])
@pytest.mark.parametrize("key", [str(VAR_UUID), "custom"])
def test_seeds_json_value_idempotently(value, key):
    variable = models.Variable(
        uuid=VAR_UUID,
        project_id=defaults.c.EM_HIDDEN_PROJECT_ID,
        name="custom",
        setter=models.SelectorVariableSetter(selector_strategy="latest"),
    )
    spec = {"variables": {key: value}}
    with (
        mock.patch.object(models.Variable, "objects") as variables,
        mock.patch.object(models.Value, "objects") as values,
        mock.patch.object(models.Value, "insert", autospec=True) as insert,
    ):
        variables.get_one_or_none.return_value = variable
        variables.get_all.return_value = [variable]
        values.get_one_or_none.side_effect = [None, mock.sentinel.existing]
        bootstrap._set_variables_from_spec(spec)
        # Switching between the name and UUID must reuse the same value.
        spec = {"variables": {str(VAR_UUID): value}}
        bootstrap._set_variables_from_spec(spec)

    insert.assert_called_once()
    inserted = insert.call_args.args[0]
    assert inserted.value == value
    assert inserted.variable is variable
    assert inserted.project_id == defaults.c.EM_HIDDEN_PROJECT_ID
    assert inserted.uuid == uuid.uuid5(
        uuid.UUID("5c8140af-4eca-4871-951e-4c939f94b39e"), str(VAR_UUID)
    )


def test_waits_for_variable_creation():
    with (
        mock.patch.object(defaults, "set_var", side_effect=[False, True]) as set_var,
        mock.patch.object(bootstrap.time, "sleep") as sleep,
    ):
        bootstrap._set_variables_from_spec({"variables": {str(VAR_UUID): False}})
    assert set_var.call_args_list == [mock.call(str(VAR_UUID), False, VAR_UUID)] * 2
    sleep.assert_called_once_with(0.5)


def test_missing_variable_times_out():
    with (
        mock.patch.object(defaults, "set_var", return_value=False),
        mock.patch.object(bootstrap.time, "monotonic", side_effect=[0, 121]),
        pytest.raises(TimeoutError, match=str(VAR_UUID)),
    ):
        bootstrap._set_variables_from_spec({"variables": {str(VAR_UUID): 1}})


@pytest.mark.parametrize("variables", [[], "invalid", None, {str(VAR_UUID): 1, "": 2}])
def test_invalid_variables_do_not_insert_values(variables):
    with (
        mock.patch.object(defaults, "set_var") as set_var,
        pytest.raises(ValueError),
    ):
        bootstrap._set_variables_from_spec({"variables": variables})
    set_var.assert_not_called()


def test_waits_for_named_variable_creation():
    with (
        mock.patch.object(models.Variable, "objects") as variables,
        mock.patch.object(defaults, "set_var", return_value=True) as set_var,
        mock.patch.object(bootstrap.time, "sleep") as sleep,
    ):
        variables.get_all.side_effect = [[], [mock.Mock(uuid=VAR_UUID)]]
        bootstrap._set_variables_from_spec({"variables": {"custom": False}})

    assert (
        variables.get_all.call_args_list
        == [mock.call(filters={"name": bootstrap.dm_filters.EQ("custom")})] * 2
    )
    set_var.assert_called_once_with(str(VAR_UUID), False, VAR_UUID)
    sleep.assert_called_once_with(0.5)


def test_missing_named_variable_times_out():
    with (
        mock.patch.object(models.Variable, "objects") as variables,
        mock.patch.object(defaults, "set_var") as set_var,
        mock.patch.object(bootstrap.time, "monotonic", side_effect=[0, 121]),
    ):
        variables.get_all.return_value = []
        with pytest.raises(TimeoutError, match="custom"):
            bootstrap._set_variables_from_spec({"variables": {"custom": 1}})
    set_var.assert_not_called()


def test_ambiguous_name_requires_uuid():
    with (
        mock.patch.object(models.Variable, "objects") as variables,
        mock.patch.object(defaults, "set_var") as set_var,
    ):
        variables.get_all.return_value = [mock.Mock(), mock.Mock()]
        with pytest.raises(ValueError, match="Ambiguous variable name.*use a UUID"):
            bootstrap._set_variables_from_spec({"variables": {"custom": 1}})
    set_var.assert_not_called()


def test_uuid_does_not_use_name_lookup():
    with (
        mock.patch.object(models.Variable, "objects") as variables,
        mock.patch.object(defaults, "set_var", return_value=True) as set_var,
    ):
        bootstrap._set_variables_from_spec({"variables": {str(VAR_UUID): 1}})
    variables.get_all.assert_not_called()
    set_var.assert_called_once_with(str(VAR_UUID), 1, VAR_UUID)
