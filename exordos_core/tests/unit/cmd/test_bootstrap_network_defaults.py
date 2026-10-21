# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0

import configparser
from pathlib import Path

import pytest

from exordos_core.cmd import bootstrap_templates
from exordos_core.common import config


@pytest.mark.parametrize(
    "persisted_text, expected",
    [
        (None, False),
        ("[DEFAULT]\ndebug = True\n", True),
        ("[network]\nlegacy_flat_default = False\n", False),
        ("[network]\nlegacy_flat_default = True\n", True),
    ],
)
def test_new_install_disables_compatibility_and_upgrade_preserves_config(
    tmp_path, persisted_text, expected
):
    persisted = tmp_path / "persisted.conf"
    if persisted_text is not None:
        persisted.write_text(persisted_text)
    destination = tmp_path / "core.conf"
    template = Path(__file__).parents[4] / "etc/exordos_core/exordos_core.conf.j2"
    bootstrap_templates._render_or_restore_template(
        str(template), str(destination), str(persisted), {"boot_ip": "10.20.0.2"}
    )
    parsed = configparser.ConfigParser()
    parsed.read(destination)
    assert (
        parsed.getboolean(
            "network",
            "legacy_flat_default",
            fallback=config.CONF.network.legacy_flat_default,
        )
        is expected
    )
    if persisted_text is not None:
        assert destination.read_text() == persisted_text
