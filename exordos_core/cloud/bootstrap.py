# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0

from importlib.metadata import version
from urllib.parse import urlsplit, urlunsplit

from gcl_sdk.cloud.bootstrap import validate_agent_config
from oslo_config import cfg

from exordos_core.common.config import CONF


def agent_config():
    """Use the advertised host and the installation's HTTP runtime API ports."""
    endpoint = CONF.boot_api.gc_boot_api
    package = "gcl_sdk==" + version("gcl_sdk")
    validate_agent_config(endpoint, endpoint, package)
    address = urlsplit(endpoint)
    hostname = address.hostname
    if ":" in hostname:
        hostname = "[" + hostname + "]"
    endpoints = []
    for group, default_port in (("orch_api", 11011), ("status_api", 11012)):
        try:
            CONF.register_opt(
                cfg.IntOpt("bind_port", default=default_port, help="API port"), group
            )
        except cfg.DuplicateOptError:
            pass
        endpoints.append(
            urlunsplit(
                ("http", f"{hostname}:{CONF[group].bind_port}", "", "", "")
            )
        )
    return *endpoints, package
