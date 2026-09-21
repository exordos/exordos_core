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

import logging
import sys

from gcl_looper.services import bjoern_service
from gcl_looper.services import hub
from oslo_config import cfg

from exordos_core.common import config
from exordos_core.common import constants as c
from exordos_core.common import log as infra_log
from exordos_core.mcp_api.api import app

api_cli_opts = [
    cfg.StrOpt(
        "bind-host",
        default=c.DEFAULT_MCP_API_HOST,
        help="The host IP to bind to",
    ),
    cfg.IntOpt(
        "bind-port",
        default=c.DEFAULT_MCP_API_PORT,
        help="The port to bind to",
    ),
    cfg.IntOpt(
        "workers",
        default=1,
        help="How many http servers should be started",
    ),
    cfg.StrOpt(
        "user_api_url",
        default=c.DEFAULT_USER_API_BASE,
        help=(
            "Base URL the User API answers '/v1/...' under. Use the address "
            "callers themselves use: the User API builds absolute URLs from "
            "the host it is asked on."
        ),
    ),
]

DOMAIN = "mcp_api"

CONF = cfg.CONF
CONF.register_cli_opts(api_cli_opts, DOMAIN)


def main():
    # Parse config
    config.parse(sys.argv[1:])

    # Configure logging
    infra_log.configure()
    log = logging.getLogger(__name__)

    log.info(
        "Start service on %s:%s, calling the User API at %s",
        CONF[DOMAIN].bind_host,
        CONF[DOMAIN].bind_port,
        CONF[DOMAIN].user_api_url,
    )

    serv_hub = hub.ProcessHubService()

    for _ in range(CONF[DOMAIN].workers):
        service = bjoern_service.BjoernService(
            wsgi_app=app.build_wsgi_application(
                user_api_url=CONF[DOMAIN].user_api_url,
            ),
            host=CONF[DOMAIN].bind_host,
            port=CONF[DOMAIN].bind_port,
            bjoern_kwargs=dict(reuse_port=True),
        )
        serv_hub.add_service(service)

    if CONF[DOMAIN].workers > 1:
        serv_hub.start()
    else:
        service.start()

    log.info("Bye!!!")


if __name__ == "__main__":
    main()
