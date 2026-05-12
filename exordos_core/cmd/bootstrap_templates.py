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
from __future__ import annotations

import ipaddress
import json
import logging
import os
import sys
import typing as tp

import jinja2
from oslo_config import cfg

from exordos_core.common import config
from exordos_core.common import constants as c
from exordos_core.secret import utils as secret_utils

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.INFO)
SPEC_PATH = "/mnt/cdrom/spec.json"


cli_opts = [
    cfg.StrOpt(
        "netplat_template_src",
        default="/opt/exordos_core/etc/90-exordos-net-config.yaml.j2",
        help="Path to the netplat source template file",
    ),
    cfg.StrOpt(
        "netplat_template_dst",
        default="/etc/netplan/90-exordos-net-base-config.yaml",
        help="Path to the netplat destination file",
    ),
    cfg.StrOpt(
        "core_config_template_src",
        default="/opt/exordos_core/etc/exordos_core/exordos_core.conf.j2",
        help="Path to the core config template",
    ),
    cfg.StrOpt(
        "core_config_template_dst",
        default="/etc/exordos_core/exordos_core.conf",
        help="Path to the core config destination file",
    ),
    cfg.StrOpt(
        "ua_config_template_src",
        default=(
            "/opt/exordos_core/etc/exordos_universal_agent/"
            "exordos_universal_agent.conf.j2"
        ),
        help="Path to the universal agent source template file",
    ),
    cfg.StrOpt(
        "ua_config_template_dst",
        default="/etc/exordos_universal_agent/exordos_universal_agent.conf",
        help="Path to the universal agent destination file",
    ),
    cfg.StrOpt(
        "resolved_template_src",
        default="/opt/exordos_core/etc/systemd/resolved.conf.j2",
        help="Path to the resolved source template file",
    ),
    cfg.StrOpt(
        "resolved_template_dst",
        default="/etc/systemd/resolved.conf",
        help="Path to the resolved destination file",
    ),
    cfg.StrOpt(
        "dnsdist_private_template_src",
        default="/opt/exordos_core/etc/dnsdist/dnsdist-private.conf.j2",
        help="Path to the dnsdist private source template file",
    ),
    cfg.StrOpt(
        "dnsdist_private_template_dst",
        default="/etc/dnsdist/dnsdist-private.conf",
        help="Path to the dnsdist private destination file",
    ),
]

CONF = cfg.CONF
CONF.register_cli_opts(cli_opts)


def _persisted_path(dst_path: str) -> str:
    dst_path = dst_path[1:] if dst_path.startswith("/") else dst_path
    return os.path.join(c.DATA_DIR, dst_path)


def _template_mappings() -> list[tuple[str, str]]:
    # Format: (source_template, destination_file, persisted_file)
    return [
        (
            CONF.netplat_template_src,
            CONF.netplat_template_dst,
            _persisted_path(CONF.netplat_template_dst),
        ),
        (
            CONF.core_config_template_src,
            CONF.core_config_template_dst,
            _persisted_path(CONF.core_config_template_dst),
        ),
        (
            CONF.ua_config_template_src,
            CONF.ua_config_template_dst,
            _persisted_path(CONF.ua_config_template_dst),
        ),
        (
            CONF.resolved_template_src,
            CONF.resolved_template_dst,
            _persisted_path(CONF.resolved_template_dst),
        ),
        (
            CONF.dnsdist_private_template_src,
            CONF.dnsdist_private_template_dst,
            _persisted_path(CONF.dnsdist_private_template_dst),
        ),
    ]


def _build_template_context(spec: dict[str, tp.Any]) -> dict[str, str]:
    stand = spec["stand"]
    bootstrap = stand["bootstraps"][0]

    main_port = next((port for port in bootstrap["ports"] if port.get("ip")), None)
    if not main_port:
        raise ValueError("Unable to detect bootstrap main interface IP from spec")

    boot_port = bootstrap["ports"][-1]

    main_network = ipaddress.ip_network(stand["network"]["cidr"])
    boot_network = ipaddress.ip_network(stand["boot_network"]["cidr"])
    boot_ip = boot_port.get("ip") or str(boot_network.network_address + 2)

    return {
        "main_mac": main_port["mac"],
        "main_ip": main_port["ip"],
        "main_ip_with_mask": f"{main_port['ip']}/{main_network.prefixlen}",
        "default_gw": str(main_network.network_address + 1),
        "boot_mac": boot_port["mac"],
        "boot_ip": boot_ip,
        "boot_ip_with_mask": f"{boot_ip}/{boot_network.prefixlen}",
        "global_salt": secret_utils.generate_salt(),
        "hs256_jwks_encryption_key": secret_utils.generate_a256gcm_key(),
        "admin_password": spec["admin_password"],
    }


def _render_or_restore_template(
    src: str, dst: str, persisted: str, context: dict[str, str] | None
) -> None:
    """
    Render template if no persisted version exists.

    Args:
        src: Source template path
        dst: Destination path
        persisted: Persisted template path
        context: Template context
    """
    if not context and not os.path.exists(persisted):
        raise RuntimeError("No context provided and no persisted template found")

    if not os.path.exists(persisted):
        with open(src, "r", encoding="utf-8") as f:
            template_source = f.read()
        rendered = jinja2.Template(template_source).render(**context)
        LOG.info("Rendered template from %s", src)
    else:
        with open(persisted, "r", encoding="utf-8") as f:
            rendered = f.read()
        LOG.info("Restored template from %s", persisted)

    if dst_dir := os.path.dirname(dst):
        os.makedirs(dst_dir, exist_ok=True)

    with open(dst, "w", encoding="utf-8") as f:
        f.write(rendered)

    LOG.info("Saved template %s -> %s", src, dst)


def main() -> None:
    # Parse config
    config.parse(sys.argv[1:])

    mappings = _template_mappings()

    if os.path.exists(SPEC_PATH):
        with open(SPEC_PATH, "r", encoding="utf-8") as f:
            spec = json.load(f)
        context = _build_template_context(spec)
        LOG.info("Built template context from spec")
    else:
        context = None
        LOG.info("Spec file not found at %s", SPEC_PATH)

    # Render template if no persisted version exists
    for src, dst, persisted in mappings:
        _render_or_restore_template(src, dst, persisted, context)
        # Save persisted version
        os.makedirs(os.path.dirname(persisted), exist_ok=True)
        with (
            open(persisted, "w", encoding="utf-8") as fp,
            open(dst, "r", encoding="utf-8") as fd,
        ):
            fp.write(fd.read())
        LOG.info("Saved persisted template %s", persisted)


if __name__ == "__main__":
    main()
