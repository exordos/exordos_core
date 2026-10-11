# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0

import json
from pathlib import Path
import runpy
from unittest import mock
import uuid

import pytest


MIGRATIONS = Path(__file__).parents[4] / "migrations"


def test_upgrade_refuses_unmaterialized_manifest_rules():
    step = runpy.run_path(str(MIGRATIONS / "0006-sdn-api-contract-15df7a.py"))[
        "migration_step"
    ]
    session = mock.Mock()
    session.execute.return_value.fetchall.return_value = []
    session.execute.return_value.fetchone.return_value = {"uuid": uuid.uuid4()}
    with pytest.raises(ValueError, match="security group rules"):
        step._canonicalize_config(session)
    assert not any(
        call.args[0].startswith("ALTER") for call in session.execute.call_args_list
    )


@pytest.mark.parametrize("table", ["ua_target_resources", "ua_actual_resources"])
def test_migration_normalizes_builder_node_snapshots(table):
    identifier = uuid.uuid4()
    row = {
        "uuid": identifier,
        "kind": "node",
        "value": {"interfaces": [{"kind": "subnet", "config": {"dhcp": False}}]},
    }
    session = mock.Mock()

    def execute(query, params):
        result = mock.Mock()
        result.fetchall.return_value = (
            [row] if query.startswith(f"SELECT uuid, kind, value FROM {table}") else []
        )
        return result

    session.execute.side_effect = execute
    migrate = runpy.run_path(str(MIGRATIONS / "0006-sdn-api-contract-15df7a.py"))
    migrate["migrate_declarations"](session)
    writes = [
        call
        for call in session.execute.call_args_list
        if call.args[0].startswith(f"UPDATE {table}")
    ]
    assert len(writes) == 1
    value = json.loads(writes[0].args[1][0])
    assert value["interfaces"] == [{"kind": "subnet", "config": {}, "dhcp": False}]
