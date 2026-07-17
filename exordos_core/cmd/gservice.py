#    Copyright 2025 Genesis Corporation.
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

from gcl_sdk.audit import opts as audit_opts
from gcl_sdk.events import opts as sdk_opts
from oslo_config import cfg
from restalchemy.common import config_opts as ra_config_opts
from restalchemy.storage.sql import engines

from exordos_core.common import config
from exordos_core.common import log as infra_log
from exordos_core.gservice.service import GeneralService

DOMAIN = "gservice"

cli_opts = [
    cfg.StrOpt(
        "lb-image",
        default="https://repo.exordos.com/exordos-lbaas/0.1.0/exordos-lbaas.raw.zst",
        help="URL to get image for LB dataplane VM",
    ),
]


CONF = cfg.CONF
ra_config_opts.register_posgresql_db_opts(CONF)
sdk_opts.register_event_opts(CONF)
audit_opts.register_audit_delivery_opts(CONF)

CONF.register_cli_opts(cli_opts, DOMAIN)


def main():
    # Parse config
    config.parse(sys.argv[1:])

    # Configure logging
    infra_log.configure()
    log = logging.getLogger(__name__)

    engines.engine_factory.configure_postgresql_factory(CONF)

    service = GeneralService()

    service.start()

    log.info("Bye!!!")


if __name__ == "__main__":
    main()
