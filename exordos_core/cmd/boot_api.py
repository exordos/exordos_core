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
import os
import pwd
import sys

from gcl_looper.services import bjoern_service
from gcl_looper.services import hub
from oslo_config import cfg
from restalchemy.common import config_opts as ra_config_opts
from restalchemy.storage.sql import engines

from exordos_core.boot_api.api import app
from exordos_core.boot_api.api import packers
from exordos_core.common import config
from exordos_core.common import log as infra_log

api_cli_opts = [
    cfg.StrOpt(
        "bind-host",
        default="127.0.0.1",
        help="The host IP to bind to",
    ),
    cfg.IntOpt(
        "bind-port",
        default=11013,
        help="The port to bind to",
    ),
    cfg.IntOpt(
        "workers",
        default=1,
        help="How many http servers should be started",
    ),
    cfg.StrOpt(
        "gc_host",
        default="core.local.genesis-core.tech",
        help="GC host",
    ),
    cfg.StrOpt(
        "gc_boot_api",
        default="http://core.local.genesis-core.tech:11013",
        help="GC boot API",
    ),
    cfg.StrOpt(
        "kernel",
        default=None,
        help="Endpoint for Linux kernel",
    ),
    cfg.StrOpt(
        "initrd",
        default=None,
        help="Endpoint for Linux initrd",
    ),
]


DOMAIN = "boot_api"

CONF = cfg.CONF
CONF.register_cli_opts(api_cli_opts, DOMAIN)
ra_config_opts.register_posgresql_db_opts(CONF)


def _prepare_nonce_store(worker_user):
    """Provision the shared replay store before the API workers drop privileges."""
    if worker_user and os.getuid() == 0:
        identity = pwd.getpwnam(worker_user)
        uid, gid = identity.pw_uid, identity.pw_gid
    else:
        uid, gid = os.geteuid(), os.getegid()
    os.makedirs(packers.BOOT_NONCES_DIR, mode=0o700, exist_ok=True)
    directory = os.open(
        packers.BOOT_NONCES_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    )
    try:
        os.fchown(directory, uid, gid)
        os.fchmod(directory, 0o700)
    finally:
        os.close(directory)


def main():
    # Parse config
    config.parse(sys.argv[1:])

    # Configure logging
    infra_log.configure()
    log = logging.getLogger(__name__)

    serv_hub = hub.ProcessHubService()

    for _ in range(CONF[DOMAIN].workers):
        service = bjoern_service.BjoernService(
            wsgi_app=app.build_wsgi_application(),
            host=CONF[DOMAIN].bind_host,
            port=CONF[DOMAIN].bind_port,
            bjoern_kwargs=dict(reuse_port=True),
        )

        _prepare_nonce_store(service.__mp_downgrade_user__)

        service.add_setup(
            lambda: engines.engine_factory.configure_postgresql_factory(conf=CONF)
        )

        serv_hub.add_service(service)

    if CONF[DOMAIN].workers > 1:
        serv_hub.start()
    else:
        service.start()

    log.info("Bye!!!")


if __name__ == "__main__":
    main()
