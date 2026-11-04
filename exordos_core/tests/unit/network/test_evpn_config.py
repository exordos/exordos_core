# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0

import subprocess
import sys


def test_boot_options_are_available_after_shared_config_initialization():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from exordos_core.common import config; "
            "from exordos_core.network.driver import evpn; "
            "assert config.CONF.boot_api.gc_host; "
            "assert config.CONF.boot_api.kernel == ''; "
            "assert config.CONF.boot_api.proxy_endpoint == ''; "
            "assert config.CONF.boot_api.gc_boot_api",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
