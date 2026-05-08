#    Copyright 2025-2026 Genesis Corporation.
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
from __future__ import annotations

import grp
import ipaddress
import json
import logging
import os
import pwd
import sys
import time
import typing as tp
import uuid as sys_uuid

from oslo_config import cfg
from restalchemy.common import config_opts as ra_config_opts
from restalchemy.dm import filters as dm_filters
from restalchemy.storage import exceptions as ra_exceptions
from restalchemy.storage.sql import engines
import yaml

from exordos_core.bootstrap import defaults as bootstrap_defaults
from exordos_core.common import config
from exordos_core.common import constants as c
from exordos_core.common import log as infra_log
from exordos_core.compute.dm import models
from exordos_core.elements.dm import models as em_models

LOG = logging.getLogger(__name__)
USER = "ubuntu"
GCTL_CFG_DIR = f"/home/{USER}/.exordos"
SPEC_PATH = "/mnt/cdrom/spec.json"
MANIFEST_PATH = "/mnt/cdrom/core.yaml"
ECOSYSTEM_REALM_MANIFEST_PATH = "/mnt/cdrom/ecosystem_realm.yaml"
MAIN_SUBNET_UUID = sys_uuid.UUID("c910a7e1-61ae-4d56-bdd6-a59faa3cbda3")


cli_opts = [
    cfg.BoolOpt(
        "retry_on_error",
        default=True,
        help="Should the script retry on errors",
    ),
    cfg.StrOpt(
        "manifest_path",
        default=MANIFEST_PATH,
        help="Path to the core manifest",
    ),
    cfg.StrOpt(
        "ecosystem_realm_manifest_path",
        default=ECOSYSTEM_REALM_MANIFEST_PATH,
        help="Path to the ecosystem realm manifest",
    ),
    cfg.StrOpt(
        "core_endpoint",
        default="http://localhost:11010",
        help="Core endpoint",
    ),
    cfg.StrOpt(
        "core_user",
        default="admin",
        help="Core user",
    ),
]

iam_opts = [
    cfg.StrOpt(
        "global_salt",
        default=None,
        help="Global salt for IAM",
    ),
    cfg.StrOpt(
        "client_secret",
        default="GenesisCoreSecret",
        help="Client secret for IAM",
    ),
    cfg.StrOpt(
        "hs256_jwks_encryption_key",
        default=c.DEFAULT_HS256_JWKS_ENCRYPTION_KEY,
        help="Encryption key for HS256 JWKS secret (A256GCM, 32 bytes)",
    ),
]

CONF = cfg.CONF
CONF.register_cli_opts(cli_opts)
CONF.register_cli_opts(iam_opts, "iam")
ra_config_opts.register_posgresql_db_opts(CONF)


def _net_range(network: ipaddress.IPv4Network, start_offset: int) -> str:
    first = network.network_address + start_offset
    size = int(network.num_addresses * 0.6) - (int(network.num_addresses * 0.6) % 10)
    last = min(first + max(size - 1, 0), network.broadcast_address - 1)
    return f"{first}-{last}"


def _apply_flat_network(stand: dict[str, tp.Any]) -> None:
    """Apply flat network configuration idempotently.

    The simplest implementation as we expect only one flat network that is created
    during the bootstrap process.
    """

    networks = models.Network.objects.get_all()
    if networks:
        LOG.info("Flat network already exists")
        return

    # Create flat network
    network_cfg = {
        "name": "flat",
        "uuid": "1d4f64db-817a-4862-a588-c9e950823cc1",
        "driver_spec": {
            "driver": "flat_bridge",
            "dhcp_cfg": "/etc/dhcp/dhcpd.conf",
        },
        "project_id": c.SERVICE_PROJECT_ID,
    }
    network = models.Network.restore_from_simple_view(**network_cfg)

    network.insert()
    LOG.info("Created network %s", network.uuid)

    #
    main_net = ipaddress.ip_network(stand["network"]["cidr"])
    boot_net = ipaddress.ip_network(stand["boot_network"]["cidr"])

    subnets = [
        {
            "name": stand["network"]["name"],
            "uuid": str(c.MAIN_SUBNET_UUID),
            "cidr": stand["network"]["cidr"],
            "ip_range": _net_range(main_net, 20),
            "ip_discovery_range": None,
            "dhcp": bool(stand["network"].get("dhcp", False)),
            "dns_servers": [str(main_net.network_address + 2)],
            "routers": [
                {
                    "to": "0.0.0.0/0",
                    "via": str(main_net.network_address + 1),
                }
            ],
            "next_server": None,
            "project_id": c.SERVICE_PROJECT_ID,
        },
        {
            "name": stand["boot_network"]["name"],
            "uuid": "86b2d256-079a-460e-a78f-bc9a7b4b2996",
            "cidr": stand["boot_network"]["cidr"],
            "ip_range": None,
            "ip_discovery_range": _net_range(boot_net, 10),
            "dhcp": bool(stand["boot_network"].get("dhcp", False)),
            "dns_servers": [str(main_net.network_address + 2)],
            "routers": [
                {
                    "to": "0.0.0.0/0",
                    "via": str(boot_net.network_address + 2),
                }
            ],
            "next_server": str(boot_net.network_address + 2),
            "project_id": c.SERVICE_PROJECT_ID,
        },
    ]

    for subnet_data in subnets:
        subnet = models.Subnet.restore_from_simple_view(**subnet_data)
        subnet.network = network.uuid
        subnet.insert()
        LOG.info("Created subnet %s", subnet.uuid)


def _apply_startup_db(spec: dict[str, tp.Any]) -> None:
    """Idempotent startup database configuration."""
    stand = spec.get("stand", {})
    if not stand:
        LOG.info("No `stand` section found in %s", spec)
        return

    # Apply flat network configuration
    _apply_flat_network(stand)

    # Apply machine pools
    # NOTE(akremenetsky): It maybe a problem for large installations
    # with many machine pools, but it's fine for now.
    machine_pools = models.MachinePool.objects.get_all()

    for hypervisor in stand.get("hypervisors", []):
        hypervisor["iface_mtu"] = 1500
        pool_data = {
            "name": "hypervisor",
            "machine_type": "VM",
            "driver_spec": hypervisor,
        }
        pool = models.MachinePool.restore_from_simple_view(**pool_data)

        # Skip if the pool already exists
        for _pool in machine_pools:
            if (
                _pool.driver_spec["connection_uri"]
                == pool.driver_spec["connection_uri"]
            ):
                break
        else:
            # Pool does not exist, create it
            try:
                pool.insert()
            except ra_exceptions.ConflictRecords:
                LOG.info("Machine pool %s already exists", pool.uuid)
            else:
                LOG.info("Created machine pool %s", pool.uuid)
            continue

        LOG.info("Machine pool %s already exists, skipping", pool.uuid)


def _ensure_gctl_config(spec: dict[str, tp.Any]):
    """Ensure gctl configuration file exists."""
    if "admin_password" not in spec:
        raise RuntimeError("No admin password found in spec")

    admin_pass = spec["admin_password"]

    os.makedirs(GCTL_CFG_DIR, exist_ok=True)
    # chown GCTL_CFG_DIR to ubuntu user
    uid = pwd.getpwnam(USER).pw_uid
    gid = grp.getgrnam(USER).gr_gid
    os.chown(GCTL_CFG_DIR, uid, gid)

    config_path = os.path.join(GCTL_CFG_DIR, "exordosctl.yaml")
    with open(config_path, "w") as f:
        yaml.safe_dump(
            {
                "schema_version": 1,
                "realms": {
                    "default-realm": {
                        "endpoint": CONF.core_endpoint,
                        "check_updates": True,
                        "contexts": {
                            "default-context": {
                                "user": CONF.core_user,
                                "password": admin_pass,
                            },
                        },
                        "current-context": "default-context",
                    }
                },
                "current-realm": "default-realm",
            },
            f,
        )
    os.chown(config_path, uid, gid)
    os.system("exordos autocomplete")


def _install_element_manifest(
    element_name: str,
    manifest_path: str,
):
    """Idempotent element manifest installation."""
    element = em_models.Element.objects.get_one_or_none(
        filters={"name": dm_filters.EQ(element_name)}
    )

    if element is not None:
        LOG.info("Element %s already installed, skipping", element_name)
        return

    if not os.path.exists(manifest_path):
        LOG.info("No manifest file found at %s", manifest_path)
        return

    os.system(
        f"exordos --config {GCTL_CFG_DIR}/exordosctl.yaml elements install {manifest_path}"
    )


def _set_defaults_vs(spec: dict[str, tp.Any]):
    """Set default values, profiles, etc."""

    tasks = [
        {"func": bootstrap_defaults.activate_profile, "args": [spec["profile"]]},
        {
            "func": bootstrap_defaults.set_core_ip_var,
            "args": [spec["stand"]["bootstraps"][0]["ports"][0]["ip"]],
        },
        {"func": bootstrap_defaults.set_ecosystem_endpoint_var, "args": [spec]},
        {"func": bootstrap_defaults.set_disable_telemetry_var, "args": [spec]},
        {"func": bootstrap_defaults.set_realm_uuid_var, "args": [spec]},
        {"func": bootstrap_defaults.set_realm_secret_var, "args": [spec]},
        {"func": bootstrap_defaults.set_realm_access_token_var, "args": [spec]},
        {"func": bootstrap_defaults.set_realm_refresh_token_var, "args": [spec]},
        {
            "func": bootstrap_defaults.set_hs256_jwks_encryption_key_var,
            "args": [CONF["iam"].hs256_jwks_encryption_key],
        },
    ]

    # Perform all tasks to set default values until timeout
    timeout_at = time.monotonic() + 120
    while tasks:
        task = tasks[0]

        # Perform task
        try:
            completed = task["func"](*task["args"])
        except Exception:
            completed = False
            LOG.exception(f"Unable to complete the task {task['func'].__name__}")

        if completed:
            tasks.pop(0)
            continue

        if time.monotonic() > timeout_at:
            raise TimeoutError(f"Timeout reached to perform {task['func'].__name__}")
        time.sleep(0.5)


def main() -> None:
    # Parse config
    config.parse(sys.argv[1:])

    # Configure logging
    infra_log.configure()

    retry_on_error = CONF.retry_on_error
    engines.engine_factory.configure_postgresql_factory(CONF)

    if not os.path.exists(SPEC_PATH):
        LOG.info("No spec file found at %s", SPEC_PATH)
        return

    with open(SPEC_PATH, "r", encoding="utf-8") as f:
        spec = json.load(f)

    bootstrap_defaults.save_developer_keys(spec.get("developer_keys", ""))

    while True:
        try:
            LOG.info("GC Bootstrap script")
            bootstrap_defaults.apply_startup_db(spec)
            bootstrap_defaults.init_secrets(
                spec, CONF["iam"].global_salt, CONF["iam"].client_secret
            )
            bootstrap_defaults.add_core_set(spec)
            _ensure_gctl_config(spec)
            _install_element_manifest("core", CONF.manifest_path)
            _install_element_manifest(
                "ecosystem_realm", CONF.ecosystem_realm_manifest_path
            )
            _set_defaults_vs(spec)
            return
        except Exception:
            LOG.exception("Unable to perform bootstrap, retrying...")
            if not retry_on_error:
                return

        time.sleep(2.0)


if __name__ == "__main__":
    main()
