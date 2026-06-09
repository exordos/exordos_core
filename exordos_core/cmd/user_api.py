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

from gcl_looper.services import bjoern_service
from gcl_looper.services import hub
from gcl_sdk import migrations as sdk_migrations
from gcl_sdk.events import clients as sdk_clients
from gcl_sdk.events import constants as event_c
from gcl_sdk.events import opts as sdk_opts
from oslo_config import cfg
from restalchemy.common import config_opts as ra_config_opts
from restalchemy.storage.sql import engines

from exordos_core.common import config
from exordos_core.common import constants as c
from exordos_core.common import log as infra_log
from exordos_core.common import utils
from exordos_core.user_api.api import app
from exordos_core.user_api.iam import drivers as iam_drivers

api_cli_opts = [
    cfg.StrOpt(
        "bind-host",
        default=c.DEFAULT_USER_API_HOST,
        help="The host IP to bind to",
    ),
    cfg.IntOpt(
        "bind-port",
        default=c.DEFAULT_USER_API_PORT,
        help="The port to bind to",
    ),
    cfg.IntOpt(
        "workers",
        default=2,
        help="How many http servers should be started",
    ),
]

iam_cli_opts = [
    cfg.StrOpt(
        "global_salt",
        default=c.DEFAULT_GLOBAL_SALT,
        help="Global salt for IAM passwords",
    ),
    cfg.StrOpt(
        "hs256_jwks_encryption_key",
        default=c.DEFAULT_HS256_JWKS_ENCRYPTION_KEY,
        help="Encryption key for HS256 JWKS secret (A256GCM, 32 bytes)",
    ),
]


DOMAIN = "user_api"
DOMAIN_IAM = "iam"


CONF = cfg.CONF
CONF.register_cli_opts(api_cli_opts, DOMAIN)
CONF.register_cli_opts(iam_cli_opts, DOMAIN_IAM)
ra_config_opts.register_posgresql_db_opts(CONF)
sdk_opts.register_event_opts(CONF)


def main():
    # Parse config
    config.parse(sys.argv[1:])

    # Configure logging
    infra_log.configure()
    log = logging.getLogger(__name__)

    sdk_migrations.apply_migrations(CONF)

    engines.engine_factory.configure_postgresql_factory(CONF)

    # Build the configured event client only when event delivery is enabled
    # (the exordos_notification element drops an [events] override into
    # /etc/exordos_core/exordos_core.d/). Otherwise use a no-op client so the
    # service neither requires an event_type_mapping file nor stores
    # unprocessable events.
    if CONF[event_c.DOMAIN].enabled:
        events_client = sdk_clients.build_client(CONF)
    else:
        events_client = sdk_clients.DummyEventClient()

    context_storage = utils.get_context_storage(
        global_salt=CONF[DOMAIN_IAM].global_salt,
        hs256_jwks_encryption_key=CONF[DOMAIN_IAM].hs256_jwks_encryption_key,
        events_client=events_client,
    )

    log.info(
        "Start service on %s:%s",
        CONF[DOMAIN].bind_host,
        CONF[DOMAIN].bind_port,
    )

    serv_hub = hub.ProcessHubService()

    for _ in range(CONF[DOMAIN].workers):
        iam_engine_driver = iam_drivers.DirectDriver()
        service = bjoern_service.BjoernService(
            wsgi_app=app.build_wsgi_application(
                context_storage=context_storage,
                iam_engine_driver=iam_engine_driver,
            ),
            host=CONF[DOMAIN].bind_host,
            port=CONF[DOMAIN].bind_port,
            bjoern_kwargs=dict(reuse_port=True),
        )

        service.add_setup(
            lambda: engines.engine_factory.configure_postgresql_factory(conf=CONF)
        )

        serv_hub.add_service(service)

    if CONF[DOMAIN].workers > 1:
        serv_hub.start()
    else:
        service.start()


if __name__ == "__main__":
    main()
