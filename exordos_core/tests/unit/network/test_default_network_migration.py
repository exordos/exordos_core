# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0

from pathlib import Path
import runpy
import uuid

import pytest


@pytest.fixture
def migration():
    return runpy.run_path(
        str(Path(__file__).parents[4] / "migrations/0010-default-network-a182bc39.py")
    )


def test_simple_networking_templates_keep_creation_intent(migration):
    convert = migration["default_network_template"]
    network, pool = str(uuid.uuid4()), str(uuid.uuid4())
    assert convert([]) == {}
    assert convert([{"port": {}}]) is None
    assert convert(
        [{"port": {"network": network}, "index": 0, "floating_ip": {"subnet": pool}}],
        template=True,
    ) == {"network": network, "floating_ip": {"subnet": pool}}


@pytest.mark.parametrize(
    "entries",
    [
        [{"port": {}}, {"port": {}}],
        [{"port": {}, "security_groups": []}],
        [{"port": {}, "identity_groups": []}],
        [{"port": {}, "index": 1}],
        [{"port": str(uuid.uuid4())}],
    ],
)
def test_unrepresentable_networking_is_not_silently_dropped(migration, entries):
    with pytest.raises(ValueError):
        migration["default_network_template"](entries, template=True)
