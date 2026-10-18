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
import typing as tp

from oslo_config import cfg

from exordos_core import version
from exordos_core.common import constants

GLOBAL_SERVICE_NAME = constants.GLOBAL_SERVICE_NAME
CONF = cfg.CONF
CONF.register_opts(
    [
        cfg.BoolOpt(
            "legacy_flat_default",
            default=True,
            help="Reuse an existing project flat subnet for automatic ports; disable to default to project SDN networks",
        )
    ],
    group="network",
)
_CONFIG_NOT_FOUND_MESSAGE = (
    "Unable to find configuration file in the"
    " default search paths (~/.%(service_name)s/, ~/,"
    " /etc/%(service_name)s/, /etc/) and the '--config-file' option!"
    % {"service_name": GLOBAL_SERVICE_NAME}
)


def parse(args, conf: tp.Optional[cfg.ConfigOpts] = None):
    if not conf:
        conf = cfg.CONF

    conf(
        args=args,
        project=GLOBAL_SERVICE_NAME,
        version="%s %s"
        % (
            GLOBAL_SERVICE_NAME.capitalize(),
            version.version_info,
        ),
    )
    if not conf.config_file:
        logging.warning(_CONFIG_NOT_FOUND_MESSAGE)
    return conf.config_file
