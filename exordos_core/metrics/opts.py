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

from oslo_config import cfg

DOMAIN = "state_metrics"

# node_exporter of the base image reads *.prom files of this directory with
# its textfile collector, the files are named after the element.
DEFAULT_TEXTFILE_PATH = "/var/lib/exordos/node_exporter/core_state.prom"

state_metrics_opts = [
    cfg.BoolOpt(
        "enabled",
        default=True,
        help="Expose platform state metrics (elements, resources, nodes, "
        "node sets and machine pools) through the node_exporter textfile "
        "collector",
    ),
    cfg.IntOpt(
        "period",
        default=15,
        min=1,
        help="How often, in seconds, the state metrics are written",
    ),
    cfg.StrOpt(
        "textfile-path",
        default=DEFAULT_TEXTFILE_PATH,
        help="File the state metrics are written to, it must be in the "
        "textfile directory of node_exporter",
    ),
]


def register_state_metrics_opts(conf=None):
    conf = conf or cfg.CONF
    conf.register_cli_opts(state_metrics_opts, DOMAIN)
