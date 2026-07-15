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
import re
import sys
import time
import typing as tp
import uuid as sys_uuid

from oslo_config import cfg
from restalchemy.common import config_opts as ra_config_opts
from restalchemy.dm import filters as dm_filters
from restalchemy.storage.sql import engines
import yaml

from exordos_core.bootstrap import defaults as bootstrap_defaults
from exordos_core.common import config
from exordos_core.common import constants as c
from exordos_core.common import log as infra_log
from exordos_core.compute.dm import models
from exordos_core.elements.dm import models as em_models
from exordos_core.repo.dm import models as repo_models

LOG = logging.getLogger(__name__)
USER = "ubuntu"
GCTL_CFG_DIR = f"/home/{USER}/.exordos"
SPEC_PATH = "/mnt/cdrom/spec.json"
MANIFEST_DIR = "/mnt/cdrom/"
REPO_COLLECTION = "/v1/repo/repositories/"
REPO_ELEMENT_COLLECTION = "/v1/repo/elements/"
BOOTSTRAP_REPO_NAME = "bootstrap_repo_975aab1b"
MAIN_SUBNET_UUID = sys_uuid.UUID("c910a7e1-61ae-4d56-bdd6-a59faa3cbda3")


cli_opts = [
    cfg.BoolOpt(
        "retry_on_error",
        default=True,
        help="Should the script retry on errors",
    ),
    cfg.StrOpt(
        "manifests_dir",
        default=MANIFEST_DIR,
        help="Directory containing manifest files",
    ),
    cfg.StrOpt(
        "core_endpoint",
        default="http://localhost/api/core",
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


def _ensure_exordos_config(spec: dict[str, tp.Any]):
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


def _ensure_repository(
    name: str,
    driver_spec: repo_models.NginxDriverSpec | repo_models.BootstrapDriverSpec,
    priority: int = 2048,
    refresh_rate: int = 3600,
    sync_mode: str = repo_models.SyncMode.LAZY.value,
    project_id: str = c.SERVICE_PROJECT_ID,
) -> repo_models.Repository:
    """Ensure repository exists and is active.

    Creates the repository if it doesn't exist and waits for it to become active.

    Args:
        name: Repository name
        driver_spec: Driver specification (NginxDriverSpec or BootstrapDriverSpec)
        priority: Repository priority (0-4096, higher = more priority)
        refresh_rate: Refresh interval in seconds (0 = disabled)
        sync_mode: Sync mode (copy or lazy)
        project_id: Project ID

    Returns:
        The active repository

    Raises:
        RuntimeError: If repository fails to become active
    """
    repository = repo_models.Repository.objects.get_one_or_none(
        filters={
            "name": dm_filters.EQ(name),
        }
    )
    if not repository:
        repository = repo_models.Repository(
            name=name,
            priority=priority,
            refresh_rate=refresh_rate,
            sync_mode=sync_mode,
            driver_spec=driver_spec,
            project_id=project_id,
        )
        repository.save()
        LOG.info("The repository is created: %s", name)

    # Wait for repository to be active
    attempts = 120
    while attempts > 0:
        repository = repo_models.Repository.objects.get_one(
            filters={"uuid": dm_filters.EQ(repository.uuid)}
        )
        if repository.status == repo_models.RepositoryStatus.ACTIVE:
            break
        time.sleep(0.5)
        attempts -= 1
    else:
        raise RuntimeError(f"Repository {name} is not active")

    LOG.info("The repository is active: %s", name)
    return repository


def _ensure_bootstrap_repo(manifests_dir: str) -> repo_models.Repository:
    """Ensure bootstrap repository exists and is active.

    Creates the bootstrap repository if it doesn't exist and waits for it
    to become active.

    Args:
        manifests_dir: Directory containing manifest files for bootstrap repo

    Returns:
        The active bootstrap repository

    Raises:
        RuntimeError: If repository fails to become active
    """
    return _ensure_repository(
        name=BOOTSTRAP_REPO_NAME,
        driver_spec=repo_models.BootstrapDriverSpec(manifests_dir=manifests_dir),
        priority=1024,
        refresh_rate=0,
        sync_mode=repo_models.SyncMode.COPY.value,
    )


MIGRATION_REPO_NAME = "migration-dummy-repo"
CORE_CONFIG_PATH = "/etc/exordos_core/exordos_core.conf"
UA_CONFIG_PATH = "/etc/exordos_universal_agent/exordos_universal_agent.conf"
CORE_CONFIG_DATA_PATH = "/var/lib/exordos/data/etc/exordos_core/exordos_core.conf"
UA_CONFIG_DATA_PATH = (
    "/var/lib/exordos/data/etc/exordos_universal_agent/exordos_universal_agent.conf"
)

_LAUNCHPAD_SECTION = """\
[launchpad]
common_registrator_opts = exordos_core.cmd.repo_proxy_gservice:register_common_opts
common_initializer = exordos_core.cmd.repo_proxy_gservice:init_common_conf
services =
    exordos_core.repo.builders.repository:RepoProxyBuilderService,
    exordos_core.repo.builders.element:RepoElementBuilderService,
    exordos_core.repo.agents.universal.service:RepoElementAgentService
"""


def _sync_config_to_data_path(content: str, data_path: str) -> None:
    """Write the same config content to the mirrored data path.

    Ensures config files under /var/lib/exordos/data/etc/ stay in sync
    with their /etc/ counterparts.
    """
    try:
        os.makedirs(os.path.dirname(data_path), exist_ok=True)
        with open(data_path, "w", encoding="utf-8") as f:
            f.write(content)
        LOG.info("Synced config to %s", data_path)
    except OSError as e:
        LOG.warning("Failed to sync config to %s: %s", data_path, e)


def _migrate_installed_elements_configs() -> None:
    """Migrate config files for repo proxy support.

    Idempotently updates two config files:

    1. Adds [launchpad] section to the core config file.
    2. Adds repo_proxy_installed_element to the capabilities list
       in the universal agent config file.
    """
    # 1. Add [launchpad] section to the core config
    try:
        with open(CORE_CONFIG_PATH, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        LOG.warning("Core config file not found: %s", CORE_CONFIG_PATH)
    else:
        if "[launchpad]" not in content:
            if not content.endswith("\n"):
                content += "\n"
            content += "\n" + _LAUNCHPAD_SECTION
            with open(CORE_CONFIG_PATH, "w", encoding="utf-8") as f:
                f.write(content)
            LOG.info("Added [launchpad] section to %s", CORE_CONFIG_PATH)
            _sync_config_to_data_path(content, CORE_CONFIG_DATA_PATH)
        else:
            LOG.info("[launchpad] section already exists in %s", CORE_CONFIG_PATH)

    # 2. Replace [universal_agent_scheduler] section in the UA config
    #    to ensure repo_proxy_installed_element is present in capabilities.
    #    The section is fully rewritten to handle any indentation differences.
    _UA_SCHEDULER_SECTION = """\
[universal_agent_scheduler]
capabilities =
    em_*,
    password,
    certificate,
    paas_lb_agent,
    repo_proxy_installed_element
"""
    try:
        with open(UA_CONFIG_PATH, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        LOG.warning("Universal agent config not found: %s", UA_CONFIG_PATH)
    else:
        new_content = re.sub(
            r"^\[universal_agent_scheduler\].*?(?=^\[|\Z)",
            _UA_SCHEDULER_SECTION,
            content,
            count=1,
            flags=re.DOTALL | re.MULTILINE,
        )
        if new_content != content:
            with open(UA_CONFIG_PATH, "w", encoding="utf-8") as f:
                f.write(new_content)
            LOG.info(
                "Updated [universal_agent_scheduler] section in %s",
                UA_CONFIG_PATH,
            )
            _sync_config_to_data_path(new_content, UA_CONFIG_DATA_PATH)
        else:
            LOG.info(
                "[universal_agent_scheduler] section already up to date in %s",
                UA_CONFIG_PATH,
            )


def _migrate_installed_elements_to_repo() -> None:
    """Migrate installed EM elements to repo proxy.

    Handles three cases:

    1. No EM elements and no repo elements — new installation, nothing
       to migrate, return early.
    2. Bootstrap repo or migration repo already exists — either a newer
       installation where migration wasn't needed, or migration was
       already performed, return early.
    3. EM elements exist but no bootstrap or migration repo — old
       installation that needs migration. Create the dummy migration
       repository and RepoElement records for each installed EM element.

    The operation is idempotent.
    """
    # Case 1: New installation — no EM elements, no repo elements
    em_elements = em_models.Element.objects.get_all()
    repo_elements = repo_models.RepoElement.objects.get_all()
    if not em_elements and not repo_elements:
        LOG.info("New installation detected, skipping migration")
        return

    # Case 2: Bootstrap repo or migration repo already exists
    existing_repos = repo_models.Repository.objects.get_all()
    existing_repo_names = {r.name for r in existing_repos}
    if (
        BOOTSTRAP_REPO_NAME in existing_repo_names
        or MIGRATION_REPO_NAME in existing_repo_names
    ):
        LOG.info("Bootstrap or migration repository already exists, skipping migration")
        return

    # Case 3: Old installation — EM elements exist but no bootstrap or
    # migration repo. Perform the migration.
    LOG.info("Old installation detected, migrating EM elements to repo proxy")

    _migrate_installed_elements_configs()

    repository = repo_models.Repository(
        name=MIGRATION_REPO_NAME,
        description="Dummy repository for migrating old installations"
        " without repo proxy",
        project_id=c.SERVICE_PROJECT_ID,
        status=repo_models.RepositoryStatus.ACTIVE.value,
        priority=0,
        refresh_rate=0,
        sync_mode=repo_models.SyncMode.LAZY.value,
        driver_spec=repo_models.DummyMigrationDriverSpec(),
    )
    repository.save()
    LOG.info("Created migration dummy repository %s", repository.uuid)

    migrated = 0
    for em_element in em_elements:
        if em_element.manifest is None:
            continue

        em_manifest = em_element.manifest

        existing = repo_models.RepoElement.objects.get_one_or_none(
            filters={"uuid": dm_filters.EQ(em_manifest.uuid)}
        )
        if existing is not None:
            continue

        manifest_dict = {
            "name": em_manifest.name,
            "version": em_manifest.version,
            "description": em_manifest.description,
            "schema_version": em_manifest.schema_version,
            "api_version": em_manifest.api_version,
            "requirements": em_manifest.requirements,
            "resources": em_manifest.resources,
            "exports": em_manifest.exports,
            "imports": em_manifest.imports,
            "openapi_spec": em_manifest.openapi_spec,
        }

        repo_element = repo_models.RepoElement(
            uuid=em_manifest.uuid,
            name=em_element.name,
            description=em_element.description,
            project_id=em_element.project_id or em_manifest.project_id,
            repository=repository,
            version=em_element.version,
            status=repo_models.RepoElementStatus.ACTIVE.value,
            installation_state=(
                repo_models.RepoElementInstallationState.INSTALLED.value
            ),
            manifest=manifest_dict,
        )
        repo_element.save()
        migrated += 1

    LOG.info("Migrated %d installed elements to repo proxy", migrated)

    public_repo = repo_models.Repository(
        name="repo.exordos.com",
        description="Public Exordos elements repository",
        project_id=c.SERVICE_PROJECT_ID,
        status=repo_models.RepositoryStatus.ACTIVE.value,
        priority=2048,
        refresh_rate=3600,
        sync_mode=repo_models.SyncMode.LAZY.value,
        driver_spec=repo_models.NginxDriverSpec(
            url="https://repo.exordos.com/exordos-elements/"
        ),
    )
    public_repo.save()
    LOG.info("Created public repository %s", public_repo.uuid)

    for service in (
        "exordos-repo-proxy-gservice.service",
        "exordos-universal-scheduler.service",
    ):
        LOG.info("Restarting service %s", service)
        os.system(f"sudo systemctl restart {service}")

    LOG.info("Waiting 10 seconds for services to start")
    time.sleep(10)

    migrated_elements = repo_models.RepoElement.objects.get_all(
        filters={"repository": dm_filters.EQ(repository.uuid)}
    )
    for element in migrated_elements:
        element.status = repo_models.RepoElementStatus.ACTIVE.value
        element.update(force=True)
    LOG.info("Force-updated %d migrated elements", len(migrated_elements))


def _ensure_repositories_from_spec(spec: dict[str, tp.Any]) -> None:
    """Ensure repositories from spec exist and are active.

    Iterates through the repository URLs in the spec and creates them
    if they don't exist.

    Args:
        spec: Specification dictionary containing repository URLs
    """
    repositories = spec.get("repository", [])
    if not repositories:
        LOG.info("No repositories found in spec")
        return

    for repo_url in repositories:
        # Generate a unique name from the URL
        repo_name = (
            repo_url.replace("https://", "")
            .replace("http://", "")
            .replace("/", "_")
            .replace(c.ELEMENTS_PATH, "")
            .strip("_")
        )

        try:
            _ensure_repository(
                name=repo_name,
                driver_spec=repo_models.NginxDriverSpec(url=repo_url),
                priority=2048,
                refresh_rate=3600,
                sync_mode=repo_models.SyncMode.LAZY.value,
            )
            LOG.info("Repository %s (%s) is active", repo_name, repo_url)
        except Exception:
            LOG.exception("Failed to ensure repository %s (%s)", repo_name, repo_url)


def _install_element_from_bootstrap_repo(element_name: str, manifests_dir: str):
    """Idempotent element manifest installation."""
    migration_repo = repo_models.Repository.objects.get_one_or_none(
        filters={"name": dm_filters.EQ(MIGRATION_REPO_NAME)}
    )
    if migration_repo is not None:
        LOG.info(
            "Migration repository exists, skipping element %s installation",
            element_name,
        )
        return

    elements = repo_models.RepoElement.objects.get_all(
        filters={
            "name": dm_filters.EQ(element_name),
            "status": dm_filters.In(
                [
                    repo_models.RepoElementStatus.ACTIVE,
                    repo_models.RepoElementStatus.IN_PROGRESS,
                    repo_models.RepoElementStatus.ERROR,
                ]
            ),
        }
    )

    if elements:
        LOG.info("Element %s already installed, skipping", element_name)
        return

    _ensure_bootstrap_repo(manifests_dir)

    # NOTE(akremenetsky): At bootstrap stage, only bootstrap repository exists,
    # so we can use simple first-match logic by element name. Other repositories
    # are not connected yet.
    elements = repo_models.RepoElement.objects.get_all(
        filters={"name": dm_filters.EQ(element_name)}
    )
    if not elements:
        raise RuntimeError(
            f"Unable to find element {element_name} in the bootstrap repository"
        )

    element = elements[0]

    # Wait for element to be available
    attempts = 60
    while attempts > 0:
        element = repo_models.RepoElement.objects.get_one(
            filters={"uuid": dm_filters.EQ(element.uuid)}
        )
        if element.status == repo_models.RepoElementStatus.AVAILABLE:
            break
        time.sleep(0.5)
        attempts -= 1
    else:
        raise RuntimeError(f"Element {element_name} is not available")

    try:
        element.install()
    except ValueError:
        LOG.warning("Element %s is already installed, skipping", element_name)


def _set_defaults_vs(spec: dict[str, tp.Any]):
    """Set default values, profiles, etc."""

    tasks = [
        {"func": bootstrap_defaults.activate_profile, "args": [spec["profile"]]},
        {
            "func": bootstrap_defaults.set_core_ip_var,
            "args": [spec["stand"]["bootstraps"][0]["ports"][0]["ip"]],
        },
        {"func": bootstrap_defaults.set_core_root_disk_size_var, "args": [spec]},
        {"func": bootstrap_defaults.set_core_data_disk_size_var, "args": [spec]},
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
        {"func": bootstrap_defaults.set_iam_default_client_uuid_var, "args": [spec]},
        {"func": bootstrap_defaults.set_iam_default_client_id_var, "args": [spec]},
        {"func": bootstrap_defaults.set_iam_default_client_secret_var, "args": [spec]},
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

    # TODO: Temporary code for RepoProxy migration. Remove this once all
    # working installations are migrated.
    _migrate_installed_elements_to_repo()

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
                spec,
                CONF["iam"].global_salt,
                spec["iam"]["default_client_id"],
                spec["iam"]["default_client_secret"],
            )
            bootstrap_defaults.add_core_set(spec)
            _ensure_exordos_config(spec)
            _install_element_from_bootstrap_repo("core", CONF.manifests_dir)
            _install_element_from_bootstrap_repo("ecosystem_realm", CONF.manifests_dir)
            _ensure_repositories_from_spec(spec)
            _set_defaults_vs(spec)
            return
        except Exception:
            LOG.exception("Unable to perform bootstrap, retrying...")
            if not retry_on_error:
                return

        time.sleep(2.0)


if __name__ == "__main__":
    main()
