# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0

import json
import re
from importlib.metadata import distribution, version
from urllib.parse import urlsplit, urlunsplit

from gcl_sdk.cloud.bootstrap import validate_agent_config
from oslo_config import cfg

from exordos_core.common.config import CONF

CONF.register_opt(
    cfg.StrOpt("agent_package", default=None, help="SDK package or wheel URL for cloud guests"),
    group="cloud_agent",
)


def agent_config():
    """Use the advertised host and the installation's HTTP runtime API ports."""
    endpoint = CONF.boot_api.gc_boot_api
    package = CONF.cloud_agent.agent_package
    if not package:
        metadata = distribution("gcl_sdk").read_text("direct_url.json")
        origin = json.loads(metadata) if metadata else {}
        vcs = origin.get("vcs_info", {})
        revision = vcs.get("commit_id", "")
        if (
            origin.get("url") == "https://github.com/exordos/gcl_sdk.git"
            and vcs.get("vcs") == "git"
            and re.fullmatch(r"[0-9a-f]{40}", revision)
        ):
            package = "git+https://github.com/exordos/gcl_sdk.git@" + revision
        else:
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
