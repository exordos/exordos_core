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
import ipaddress
import json
import subprocess
import shlex
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
from exordos_core.boot_api.api import proxy
from exordos_core.common import config
from exordos_core.common import log as infra_log

api_cli_opts = [
    cfg.StrOpt(
        "proxy-bind-host",
        default=None,
        help="Encrypted metadata proxy listener address",
    ),
    cfg.IntOpt(
        "proxy-bind-port", default=11015, help="Encrypted metadata proxy listener port"
    ),
    cfg.StrOpt(
        "proxy_endpoint",
        default="",
        help="Encrypted metadata proxy endpoint hypervisors use",
    ),
    cfg.StrOpt(
        "dhcp_leases_file",
        default="/var/lib/dhcp/dhcpd.leases",
        help="Authoritative ISC Boot leases",
    ),
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


def _prepare_boot_firewall():
    """Legacy Seed traffic may enter only through the interface owning Boot IP."""
    address = ipaddress.IPv4Address(CONF[DOMAIN].bind_host)
    if address.is_loopback:
        return
    devices = json.loads(
        subprocess.check_output(["ip", "-json", "-4", "address", "show"], text=True)
    )
    interfaces = [
        d["ifname"]
        for d in devices
        if any(a.get("local") == str(address) for a in d.get("addr_info", []))
    ]
    if len(interfaces) != 1:
        raise ValueError("Boot API must bind one locally assigned Boot address")
    # Reject unknown interface state before touching the existing chain.
    interface = interfaces[0]
    chain = "EXORDOS_BOOT_API"
    rules = (
        "*filter\n:%s - [0:0]\n-F %s\n-A %s -i %s -d %s/32 -j RETURN\n-A %s -j DROP\nCOMMIT\n"
        % (chain, chain, chain, interface, address, chain)
    )
    subprocess.run(
        ["iptables-restore", "--noflush"], input=rules, text=True, check=True
    )
    desired = ["-p", "tcp", "--dport", str(CONF[DOMAIN].bind_port), "-j", chain]
    if subprocess.run(
        ["iptables", "-C", "INPUT", *desired], capture_output=True
    ).returncode:
        subprocess.run(["iptables", "-I", "INPUT", "1", *desired], check=True)
    # Remove only obsolete hooks owned by this service after the new one exists.
    current = subprocess.check_output(["iptables", "-S", "INPUT"], text=True)
    for line in current.splitlines():
        fields = shlex.split(line)
        if (
            fields[-2:] == ["-j", chain]
            and fields[2:] != desired
            and fields[2:] != ["-p", "tcp", "-m", "tcp", *desired[2:]]
        ):
            subprocess.run(["iptables", "-D", "INPUT", *fields[2:]], check=True)


def main():
    # Parse config
    config.parse(sys.argv[1:])

    # Configure logging
    infra_log.configure()
    log = logging.getLogger(__name__)

    _prepare_boot_firewall()
    serv_hub = hub.ProcessHubService()

    application = app.build_wsgi_application()
    listeners = [(CONF[DOMAIN].bind_host, CONF[DOMAIN].bind_port, application)]
    if CONF[DOMAIN].proxy_bind_host:
        listeners.append(
            (
                CONF[DOMAIN].proxy_bind_host,
                CONF[DOMAIN].proxy_bind_port,
                proxy.BootProxyMiddleware(application),
            )
        )
    for host, port, application in listeners * CONF[DOMAIN].workers:
        service = bjoern_service.BjoernService(
            wsgi_app=application,
            host=host,
            port=port,
            bjoern_kwargs=dict(reuse_port=True),
        )

        _prepare_nonce_store(service.__mp_downgrade_user__)

        service.add_setup(
            lambda: engines.engine_factory.configure_postgresql_factory(conf=CONF)
        )

        serv_hub.add_service(service)

    if len(listeners) * CONF[DOMAIN].workers > 1:
        serv_hub.start()
    else:
        service.start()

    log.info("Bye!!!")


if __name__ == "__main__":
    main()
