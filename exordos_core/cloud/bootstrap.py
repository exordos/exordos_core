# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0

from importlib.metadata import version

from gcl_sdk.cloud.bootstrap import validate_agent_config

from exordos_core.common.config import CONF


def agent_config():
    """Use the installation's combined Boot/Orch/Status entry point."""
    endpoint = CONF.boot_api.gc_boot_api
    package = "gcl_sdk==" + version("gcl_sdk")
    validate_agent_config(endpoint, endpoint, package)
    return endpoint, endpoint, package
