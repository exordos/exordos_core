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
import grp
import ipaddress
import logging
import os
import pathlib
import pwd
import re
import subprocess
import tempfile
import typing as tp
import uuid as sys_uuid

from gcl_sdk.agents.universal import constants as ua_constants
from gcl_sdk.agents.universal import utils as ua_utils
from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.agents.universal.drivers import pool as ua_pool
from gcl_sdk.infra.dm import models as infra_models
from restalchemy.dm import filters as dm_filters
from restalchemy.storage import exceptions as ra_exceptions

from exordos_core.common import constants as c
from exordos_core.compute import constants as nc
from exordos_core.compute.dm import models as compute_models
from exordos_core.compute.node_set.dm import models as node_set_models
from exordos_core.elements.dm import models as element_models
from exordos_core.secret import utils as secret_utils
from exordos_core.user_api.iam.dm import models as iam_models
from exordos_core.vs.dm import models as vs_models

LOG = logging.getLogger(__name__)
USER = "ubuntu"


def set_var(
    name: str,
    value: tp.Any,
    var_uuid: sys_uuid.UUID,
    val_uuid: sys_uuid.UUID | None = None,
) -> bool:
    if val_uuid is None:
        namespace = sys_uuid.UUID("5c8140af-4eca-4871-951e-4c939f94b39e")
        val_uuid = sys_uuid.uuid5(namespace, name)

    existing_value = vs_models.Value.objects.get_one_or_none(
        filters={"uuid": dm_filters.EQ(val_uuid)}
    )
    if existing_value:
        LOG.info("The value for the '%s' variable already exists", name)
        return True

    LOG.info("Set %s variable", name)
    var = vs_models.Variable.objects.get_one_or_none(
        filters={"uuid": dm_filters.EQ(var_uuid)}
    )

    # The variable hasn't been created, skip
    if not var:
        return False

    val = vs_models.Value(
        uuid=val_uuid,
        variable=var,
        value=value,
        project_id=c.EM_HIDDEN_PROJECT_ID,
    )
    val.insert()
    return True


def seed_core_set_target(node_set):
    """Keep the bootstrap set desired while profile values are resolving."""
    resource = element_models.Resource.objects.get_one(
        filters={"uuid": dm_filters.EQ(node_set.uuid)}
    )
    if resource.target_resource is not None:
        return
    target = ua_models.TargetResourceMixin.to_ua_resource(node_set, kind=resource.kind)
    target.insert()
    resource.target_resource = target
    resource.update()


def add_core_set(
    spec: dict[str, tp.Any], set_active: bool = False
) -> "node_set_models.NodeSet":
    """Idempotent core set and nodes addition.

    We need to add the core set and nodes to the database
    to make the bootstrap process idempotent. When the core manifest
    is installed, the core set and nodes will be detected on the data
    plane and won't be created again.
    """
    existing = node_set_models.NodeSet.objects.get_one_or_none(
        filters={"uuid": dm_filters.EQ(c.CORE_SET_UUID)}
    )
    if existing is not None:
        seed_core_set_target(existing)
        return existing
    disks = []
    for idx, disk in enumerate(spec["stand"]["bootstraps"][0]["disks"]):
        # The first disk is the system disk with an image
        if idx == 0:
            disks.append(
                {
                    "size": disk["size"],
                    "label": disk["label"],
                    "image": spec["stand"]["bootstraps"][0]["image_uri"],
                }
            )
        else:
            disks.append({"size": disk["size"], "label": disk["label"]})

    node_set = node_set_models.NodeSet(
        uuid=c.CORE_SET_UUID,
        name="core-set",
        project_id=c.EM_PROJECT_ID,
        cores=spec["stand"]["bootstraps"][0]["cores"],
        ram=spec["stand"]["bootstraps"][0]["memory"],
        disk_spec=infra_models.SetDisksSpec(
            disks=disks,
        ),
        # It will be reworked after HA is implemented
        replicas=1,
    )
    node_set.insert()

    if set_active:
        node_set.set_active()
    seed_core_set_target(node_set)
    # Convert instance to resource
    node_set_resource = node_set.to_ua_resource()
    node_set_resource.insert()

    LOG.info("Created node set %s", node_set.uuid)

    # Add nodes
    nodes = node_set.gen_nodes(
        project_id=nc.NODE_SET_PROJECT,
        placement_policies=[],
        node_uuids=[sys_uuid.UUID(spec["stand"]["bootstraps"][0]["uuid"])],
    )

    for i, node in enumerate(nodes):
        bootstrap_spec = spec["stand"]["bootstraps"][i]

        # Keep the model name: pool drivers add their domain prefix themselves.
        # The core name also prevents reflashing this running bootstrap node.
        # Bootstrap supplies the known ports below, bypassing automatic placement.
        node.default_network = {}
        node._insert_node()

        if set_active:
            node.set_active()

        # Convert derivatives to resources
        node_resource = node.to_ua_resource(master=node_set_resource.uuid)
        node_resource.insert()
        LOG.info("Created node %s", node.uuid)

        # Create ports.
        for port in bootstrap_spec["ports"]:
            if not port.get("ip"):
                continue

            p = compute_models.Port.restore_from_simple_view(
                **dict(
                    subnet=str(c.MAIN_SUBNET_UUID),
                    source=spec["stand"]["network"]["name"],
                    node=str(node.uuid),
                    ipv4=port["ip"],
                    mac=port["mac"],
                    status="ACTIVE",
                    project_id=str(node_set.project_id),
                    owner_node=str(node.uuid),
                )
            )
            p.insert()
    return node_set


def save_developer_keys(keys: str) -> None:
    """Save developer keys from the spec."""
    if not keys:
        LOG.debug("No developer keys provided.")
        return

    pw = pwd.getpwnam(USER)
    uid, gid = pw.pw_uid, grp.getgrnam(USER).gr_gid
    ssh_dir = pathlib.Path(pw.pw_dir) / ".ssh"

    ssh_dir.mkdir(parents=True, exist_ok=True)
    os.chown(ssh_dir, uid, gid)
    ssh_dir.chmod(0o700)

    keys_path = ssh_dir / "authorized_keys"
    content = keys_path.read_text() if keys_path.exists() else ""
    if keys not in content:
        with keys_path.open("a") as f:
            if content and not content.endswith("\n"):
                f.write("\n")
            f.write(keys + "\n")
        os.chown(keys_path, uid, gid)
        keys_path.chmod(0o600)


def activate_profile(profile_name: str) -> bool:
    # Check if there is already an active profile
    active_profile = vs_models.Profile.objects.get_one_or_none(
        filters={
            "active": dm_filters.EQ(True),
        },
    )
    if active_profile:
        LOG.info("Already has active profile: %s", active_profile.name)
        return True

    LOG.info("Activating default profile")
    profile = vs_models.Profile.objects.get_one_or_none(
        filters={
            "name": dm_filters.EQ(profile_name),
        },
    )
    if profile:
        profile.activate()
        LOG.info("Activated profile: %s", profile.name)
        return True

    return False


def set_core_ip_var(value: tp.Any) -> bool:
    existing_value = vs_models.Value.objects.get_one_or_none(
        filters={"uuid": dm_filters.EQ(c.VALUE_CORE_IP_ADDRESS_UUID)}
    )
    if existing_value:
        LOG.info("Core IP address variable already exists")
        return True

    LOG.info("Set core_ip_address variable")
    core_ip_var = vs_models.Variable.objects.get_one_or_none(
        filters={"uuid": dm_filters.EQ(c.VAR_CORE_IP_ADDRESS_UUID)}
    )
    if not core_ip_var:
        return False

    core_ip_value = vs_models.Value(
        uuid=c.VALUE_CORE_IP_ADDRESS_UUID,
        variable=core_ip_var,
        # Get IP from the main network
        value=value,
        project_id=c.EM_HIDDEN_PROJECT_ID,
    )
    core_ip_value.insert()
    return True


def init_secrets(
    spec: dict[str, tp.Any], global_salt: str, client_id: str, client_secret: str
) -> None:
    """Idempotent secrets initialization."""
    # Check if the secrets are already initialized
    default_client = iam_models.IamClient.objects.get_one_or_none(
        filters={"salt": dm_filters.EQ(c.DEFAULT_ADMIN_SALT)}
    )
    if default_client is None:
        LOG.info(
            "IAM client with default salt not found, secrets initialization skipped"
        )
        return None

    if not global_salt:
        raise ValueError("Global salt is not provided")

    if "admin_password" not in spec:
        raise RuntimeError("No admin password found in spec")

    admin_pass = spec["admin_password"]

    # Regenerate secrets for the default IAM client
    admin_salt = secret_utils.generate_salt()
    client_secret_hash = secret_utils.generate_hash_for_secret(
        secret=client_secret,
        secret_salt=admin_salt,
        global_salt=global_salt,
    )
    default_client.salt = admin_salt
    default_client.secret_hash = client_secret_hash
    default_client.client_id = client_id
    default_client.save()

    # Regenerate secrets for the default IAM user
    admin_secret_hash = secret_utils.generate_hash_for_secret(
        secret=admin_pass,
        secret_salt=admin_salt,
        global_salt=global_salt,
    )
    default_user = iam_models.User.objects.get_one(
        filters={"salt": dm_filters.EQ(c.DEFAULT_ADMIN_SALT)}
    )
    default_user.salt = admin_salt
    default_user.secret_hash = admin_secret_hash
    default_user.save()

    return None


def _net_range(network: ipaddress.IPv4Network, start_offset: int) -> str:
    first = network.network_address + start_offset
    size = int(network.num_addresses * 0.6) - (int(network.num_addresses * 0.6) % 10)
    last = min(first + max(size - 1, 0), network.broadcast_address - 1)
    return f"{first}-{last}"


def apply_flat_network(stand: dict[str, tp.Any]) -> None:
    """Apply flat network configuration idempotently.

    The simplest implementation as we expect only one flat network that is created
    during the bootstrap process.
    """

    networks = compute_models.Network.objects.get_all()
    if networks:
        LOG.info("Flat network already exists")
        return

    # Create flat network
    network_cfg = {
        "name": "flat",
        "uuid": c.NETWORK_UUID,
        "driver_spec": {
            "driver": "flat_bridge",
            "dhcp_cfg": "/etc/dhcp/dhcpd.conf",
        },
        "project_id": c.ZERO_UUID,
    }
    network = compute_models.Network.restore_from_simple_view(**network_cfg)

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
            "project_id": c.ZERO_UUID,
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
            "project_id": c.ZERO_UUID,
        },
    ]

    for subnet_data in subnets:
        subnet = compute_models.Subnet.restore_from_simple_view(**subnet_data)
        subnet.network = network.uuid
        subnet.insert()
        LOG.info("Created subnet %s", subnet.uuid)


def overlay_is_wanted(spec: tp.Optional[dict[str, tp.Any]]) -> tp.Optional[bool]:
    """Whether an overlay exists or an explicit route reflector is configured."""
    stand = (spec or {}).get("stand") or {}
    if stand.get("evpn_rr_address"):
        return True
    try:
        for network in compute_models.Network.objects.get_all():
            if nc.is_overlay(network.driver_spec):
                return True
    except Exception:
        # Unknown: answer the safe way; the next pass asks again.
        LOG.exception("Cannot tell whether this installation carries an overlay")
        return None
    return False


EVPN_RR_CONF_PATH = "/etc/exordos_core/exordos_core.d/evpn.conf"


def render_evpn_rr_config(
    spec: dict[str, tp.Any], *, overlay_wanted: tp.Optional[bool] = None
) -> tp.Optional[str]:
    """Render the [evpn] route-reflector config for the network service.

    The core node is the route reflector. Returns ``None`` when the stand
    lacks the data or wants no overlay (the drop-in costs an `ec-gservice`
    restart).
    """
    if overlay_wanted is None:
        overlay_wanted = overlay_is_wanted(spec)
    if not overlay_wanted:
        return None
    stand = spec.get("stand", {})
    bootstraps = stand.get("bootstraps") or []
    network = stand.get("network") or {}
    if not bootstraps or not network.get("cidr"):
        return None
    core = bootstraps[0]
    core_ip = next((p["ip"] for p in core.get("ports", []) if p.get("ip")), None)
    if not core.get("uuid") or not core_ip:
        return None
    # In a multi-hypervisor realm the extra hypervisors reach the RR through
    # the control node's flat address (Border forwards 179):
    # ``evpn_rr_address`` overrides what clients peer with, and
    # ``evpn_peer_prefixes`` adds the prefixes they peer from.
    rr_address = stand.get("evpn_rr_address") or core_ip
    peer_prefixes = [network["cidr"]]
    for extra in stand.get("evpn_peer_prefixes") or []:
        if extra not in peer_prefixes:
            peer_prefixes.append(extra)
    # Extra hypervisors reach DNS and boot services through the same Border.
    content = (
        "[evpn]\n"
        "rr_agent = %(agent)s\n"
        "rr_addresses = %(rr)s\n"
        "rr_peer_prefixes = %(prefixes)s\n"
        "dns_forwarders = %(ip)s\n"
    ) % {
        "agent": core["uuid"],
        "rr": rr_address,
        "prefixes": ",".join(peer_prefixes),
        "ip": rr_address,
    }
    content += (
        "\n[boot_api]\n"
        "proxy_bind_host = 0.0.0.0\n"
        "proxy_endpoint = http://%(ip)s:11015\n"
        "kernel = http://%(ip)s:8080/bios/vmlinuz\n"
        "initrd = http://%(ip)s:8080/bios/initrd.img\n"
    ) % {"ip": rr_address}
    return content


def apply_evpn_rr_config(spec: dict[str, tp.Any]) -> None:
    """Write the [evpn] RR drop-in idempotently and refresh the service."""
    overlay_wanted = overlay_is_wanted(spec)
    if overlay_wanted is None:
        return
    content = render_evpn_rr_config(spec, overlay_wanted=overlay_wanted)
    if overlay_wanted and content is None:
        return
    previous = ""
    try:
        with open(EVPN_RR_CONF_PATH) as f:
            previous = f.read()
            if previous == content:
                return
    except FileNotFoundError:
        if content is None:
            return
    if content is None:
        os.unlink(EVPN_RR_CONF_PATH)
    else:
        os.makedirs(os.path.dirname(EVPN_RR_CONF_PATH), exist_ok=True)
        with open(EVPN_RR_CONF_PATH, "w") as f:
            f.write(content)
    LOG.info("Updated evpn RR config %s", EVPN_RR_CONF_PATH)
    # ec-gservice hosts the network service; nudge it to reread the config.
    try:
        services = ["ec-gservice"]
        if "[boot_api]" in previous or "[boot_api]" in (content or ""):
            services.append("ec-boot-api")
        subprocess.run(["systemctl", "try-restart", *services], check=False)
    except (OSError, subprocess.SubprocessError) as e:
        LOG.warning("Could not restart ec-gservice for evpn RR config: %s", e)


def apply_startup_db(spec: dict[str, tp.Any]) -> None:
    """Idempotent startup database configuration."""
    stand = spec.get("stand", {})
    if not stand:
        LOG.info("No `stand` section found in %s", spec)
        return

    # Apply flat network configuration
    apply_flat_network(stand)

    # Configure the EVPN route reflector (the core node)
    apply_evpn_rr_config(spec)

    # Apply machine pools
    # NOTE(akremenetsky): It maybe a problem for large installations
    # with many machine pools, but it's fine for now.
    machine_pools = compute_models.MachinePool.objects.get_all()

    for hypervisor in stand.get("hypervisors", []):
        hypervisor["iface_mtu"] = 1500
        # The agent encryption key isn't part of the driver spec, it's the
        # symmetric key already written to the local agent's disk by the
        # CLI, so it must be popped off before the driver spec is built.
        agent_private_key = hypervisor.pop("private_key", None)
        # Drop unset optional fields (e.g. `node`, only used by the
        # "exordos_local_hyper" kind) so they don't trip up spec kinds
        # that don't define them.
        hypervisor = {k: v for k, v in hypervisor.items() if v is not None}
        if hypervisor.get("kind") == "exordos_local_hyper" and isinstance(
            hypervisor.get("storage_pool"), str
        ):
            hypervisor["storage_pool"] = (
                [{"name": hypervisor["storage_pool"]}]
                if hypervisor["storage_pool"]
                else []
            )
        pool_data = {
            "name": "hypervisor",
            "machine_type": "VM",
            "driver_spec": hypervisor,
        }
        pool = compute_models.MachinePool.restore_from_simple_view(**pool_data)

        # Skip if the pool already exists
        for _pool in machine_pools:
            if _pool.driver_spec.connection_uri == pool.driver_spec.connection_uri:
                break
        else:
            # Pool does not exist, create it
            try:
                pool.insert()
            except ra_exceptions.ConflictRecords:
                LOG.info("Machine pool %s already exists", pool.uuid)
            else:
                # A local hypervisor's agent authenticates to the
                # orch/status APIs with a node encryption key, so
                # provision one for its node uuid, in sync with the key
                # already written to the agent's disk by the CLI.
                if isinstance(pool.driver_spec, ua_pool.ExordosLocalHyperDriverSpec):
                    ua_models.NodeEncryptionKey.get_or_create(
                        pool.driver_spec.node,
                        private_key=agent_private_key,
                    )
                LOG.info("Created machine pool %s", pool.uuid)
            continue

        LOG.info("Machine pool %s already exists, skipping", pool.uuid)


def set_ecosystem_endpoint_var(spec: dict[str, tp.Any]) -> bool:
    val_uuid = sys_uuid.UUID("0a810628-544e-4a1a-a895-d5e4176eec06")
    existing_value = vs_models.Value.objects.get_one_or_none(
        filters={"uuid": dm_filters.EQ(val_uuid)}
    )
    if existing_value:
        LOG.info("Ecosystem endpoint variable already exists")
        return True

    LOG.info("Set ecosystem_endpoint variable")
    ecosystem_endpoint_var = vs_models.Variable.objects.get_one_or_none(
        filters={"uuid": dm_filters.EQ(c.VAR_ECOSYSTEM_ENDPOINT_UUID)}
    )
    if not ecosystem_endpoint_var:
        return False

    endpoint_value = spec.get("ecosystem_endpoint", "")
    ecosystem_endpoint_value = vs_models.Value(
        uuid=val_uuid,
        variable=ecosystem_endpoint_var,
        value=endpoint_value,
        project_id=c.EM_HIDDEN_PROJECT_ID,
    )
    ecosystem_endpoint_value.insert()
    return True


def set_disable_telemetry_var(spec: dict[str, tp.Any]) -> bool:
    val_uuid = sys_uuid.UUID("4c4b1ce8-4d39-4000-a826-098053d796e4")
    existing_value = vs_models.Value.objects.get_one_or_none(
        filters={"uuid": dm_filters.EQ(val_uuid)}
    )
    if existing_value:
        LOG.info("Disable telemetry variable already exists")
        return True

    LOG.info("Set disable_telemetry variable")
    disable_telemetry_var = vs_models.Variable.objects.get_one_or_none(
        filters={"uuid": dm_filters.EQ(c.VAR_DISABLE_TELEMETRY_UUID)}
    )
    if not disable_telemetry_var:
        return False

    # Get disable telemetry flag from spec or use default False
    telemetry_value = spec.get("disable_telemetry", False)

    disable_telemetry_value = vs_models.Value(
        uuid=val_uuid,
        variable=disable_telemetry_var,
        value=telemetry_value,
        project_id=c.EM_HIDDEN_PROJECT_ID,
    )
    disable_telemetry_value.insert()
    return True


def set_realm_uuid_var(spec: dict[str, tp.Any]) -> bool:
    val_uuid = sys_uuid.UUID("f134c97f-bb4d-4d67-9527-8c90e2f04f8c")
    existing_value = vs_models.Value.objects.get_one_or_none(
        filters={"uuid": dm_filters.EQ(val_uuid)}
    )
    if existing_value:
        LOG.info("Stand UUID variable already exists")
        return True

    LOG.info("Set realm_uuid variable")
    realm_uuid_var = vs_models.Variable.objects.get_one_or_none(
        filters={"uuid": dm_filters.EQ(c.VAR_REALM_UUID_UUID)}
    )
    if not realm_uuid_var:
        return False

    # Get stand UUID from spec
    realm_uuid_value = spec.get("realm_uuid", "")
    if not realm_uuid_value:
        LOG.warning("No stand UUID found in spec")
        return True

    realm_uuid_val = vs_models.Value(
        uuid=val_uuid,
        variable=realm_uuid_var,
        value=realm_uuid_value,
        project_id=c.EM_HIDDEN_PROJECT_ID,
    )
    realm_uuid_val.insert()
    return True


def set_realm_secret_var(spec: dict[str, tp.Any]) -> bool:
    val_uuid = sys_uuid.UUID("c5548ad1-7990-4283-ba1f-725fd7a7b9d4")
    existing_value = vs_models.Value.objects.get_one_or_none(
        filters={"uuid": dm_filters.EQ(val_uuid)}
    )
    if existing_value:
        LOG.info("Stand secret variable already exists")
        return True

    LOG.info("Set realm_secret variable")
    realm_secret_var = vs_models.Variable.objects.get_one_or_none(
        filters={"uuid": dm_filters.EQ(c.VAR_REALM_SECRET_UUID)}
    )
    if not realm_secret_var:
        return False

    # Get stand secret from spec
    realm_secret_value = spec.get("realm_secret", "")
    if not realm_secret_value:
        LOG.warning("No stand secret found in spec")
        return True

    realm_secret_val = vs_models.Value(
        uuid=val_uuid,
        variable=realm_secret_var,
        value=realm_secret_value,
        project_id=c.EM_HIDDEN_PROJECT_ID,
    )
    realm_secret_val.insert()
    return True


def set_realm_access_token_var(spec: dict[str, tp.Any]) -> bool:
    val_uuid = sys_uuid.UUID("2e0a0f6f-0568-4804-91d2-1f68f43afda9")
    existing_value = vs_models.Value.objects.get_one_or_none(
        filters={"uuid": dm_filters.EQ(val_uuid)}
    )
    if existing_value:
        LOG.info("Stand access token variable already exists")
        return True

    LOG.info("Set realm_access_token variable")
    realm_access_token_var = vs_models.Variable.objects.get_one_or_none(
        filters={"uuid": dm_filters.EQ(c.VAR_REALM_ACCESS_TOKEN_UUID)}
    )
    if not realm_access_token_var:
        return False

    # Get stand access token from spec (could be a dict or string)
    token_value = spec.get("realm_tokens", {})
    if not token_value:
        LOG.warning("No stand access token found in spec")
        return True

    realm_access_token_val = vs_models.Value(
        uuid=val_uuid,
        variable=realm_access_token_var,
        value=token_value.get("access_token", ""),
        project_id=c.EM_HIDDEN_PROJECT_ID,
    )
    realm_access_token_val.insert()
    return True


def set_realm_refresh_token_var(spec: dict[str, tp.Any]) -> bool:
    val_uuid = sys_uuid.UUID("eacf0c1f-3495-4986-89a5-80139526b82a")
    existing_value = vs_models.Value.objects.get_one_or_none(
        filters={"uuid": dm_filters.EQ(val_uuid)}
    )
    if existing_value:
        LOG.info("Stand refresh token variable already exists")
        return True

    LOG.info("Set realm_refresh_token variable")
    realm_refresh_token_var = vs_models.Variable.objects.get_one_or_none(
        filters={"uuid": dm_filters.EQ(c.VAR_REALM_REFRESH_TOKEN_UUID)}
    )
    if not realm_refresh_token_var:
        return False

    # Get stand refresh token from spec (could be a dict or string)
    token_value = spec.get("realm_tokens", {})
    if not token_value:
        LOG.warning("No stand refresh token found in spec")
        return True

    realm_refresh_token_val = vs_models.Value(
        uuid=val_uuid,
        variable=realm_refresh_token_var,
        value=token_value.get("refresh_token", ""),
        project_id=c.EM_HIDDEN_PROJECT_ID,
    )
    realm_refresh_token_val.insert()
    return True


def set_hs256_jwks_encryption_key_var(hs256_jwks_encryption_key: str) -> bool:
    val_uuid = sys_uuid.UUID("b16ba886-c18d-4842-9815-ba09d2aae2cb")
    existing_value = vs_models.Value.objects.get_one_or_none(
        filters={"uuid": dm_filters.EQ(val_uuid)}
    )
    if existing_value:
        LOG.info("HS256 JWKS encryption key variable already exists")
        return True

    LOG.info("Set hs256_jwks_encryption_key variable")
    hs256_var = vs_models.Variable.objects.get_one_or_none(
        filters={"uuid": dm_filters.EQ(c.VAR_HS256_JWKS_ENCRYPTION_KEY_UUID)}
    )
    if not hs256_var:
        return False

    key_value = hs256_jwks_encryption_key
    if not key_value:
        LOG.warning("No HS256 JWKS encryption key provided")
        return True

    hs256_val = vs_models.Value(
        uuid=val_uuid,
        variable=hs256_var,
        value=key_value,
        project_id=c.EM_HIDDEN_PROJECT_ID,
    )
    hs256_val.insert()
    return True


def set_core_root_disk_size_var(spec: dict) -> bool:
    return set_var(
        "core_root_disk_size",
        spec["stand"]["bootstraps"][0]["disks"][0]["size"],
        c.VAR_ROOT_DISK_UUID,
    )


def set_core_data_disk_size_var(spec: dict) -> bool:
    return set_var(
        "core_data_disk_size",
        spec["stand"]["bootstraps"][0]["disks"][1]["size"],
        c.VAR_DATA_DISK_UUID,
    )


def set_iam_default_client_uuid_var(spec: dict) -> bool:
    return set_var(
        "iam_default_client_uuid",
        spec["iam"]["default_client_uuid"],
        c.VAR_IAM_DEFAULT_CLIENT_UUID,
    )


def set_iam_default_client_id_var(spec: dict) -> bool:
    return set_var(
        "iam_default_client_id",
        spec["iam"]["default_client_id"],
        c.VAR_IAM_DEFAULT_CLIENT_ID_UUID,
    )


def set_iam_default_client_secret_var(spec: dict) -> bool:
    return set_var(
        "iam_default_client_secret",
        spec["iam"]["default_client_secret"],
        c.VAR_IAM_DEFAULT_CLIENT_SECRET_UUID,
    )


# --- the universal agent's own config ------------------------------------
#
# Not in `cmd/bootstrap.py`: importing it registers CLI options, which kills
# a running service with `ArgsAlreadyParsedError`, and the reconcile loop
# needs this code.

UA_CONFIG_PATH = "/etc/exordos_universal_agent/exordos_universal_agent.conf"
UA_CONFIG_DATA_PATH = (
    "/var/lib/exordos/data/etc/exordos_universal_agent/exordos_universal_agent.conf"
)
# Present when a dedicated SDN agent serves another installation's overlay
# (`exordos compute hypervisors register-agent --write-config`). The two
# evpn drivers would share one meta file, so the node's own agent drops evpn.
SDN_AGENT_CONFIG_PATH = "/etc/exordos_universal_agent/sdn_agent.conf"


def ensure_local_ua_transport() -> None:
    """Move Core's own agent from Boot API to authenticated local APIs.

    The Core Boot interface has no persisted Port. Source-port identity on
    Boot API therefore cannot authenticate the local runtime agent.
    """
    path = pathlib.Path(UA_CONFIG_PATH)
    content = path.read_text(encoding="utf-8")
    path.chmod(0o600)
    mirror = pathlib.Path(UA_CONFIG_DATA_PATH)
    if mirror.exists():
        mirror.chmod(0o600)
    key = ua_models.NodeEncryptionKey.get_or_create(ua_utils.system_uuid())
    key_path = pathlib.Path(ua_constants.PRIVATE_KEY_PATH)
    key_changed = (
        not key_path.exists() or key_path.read_text().strip() != key.private_key
    )
    if key_changed:
        key_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", dir=key_path.parent, delete=False
        ) as f:
            f.write(key.private_key)
        os.replace(f.name, key_path)
    key_path.chmod(0o600)

    match = re.search(
        r"^\[universal_agent\].*?(?=^\[|\Z)", content, re.MULTILINE | re.DOTALL
    )
    if match is None:
        raise ValueError("Missing universal_agent config section")
    section = match.group(0)
    for name, value in {
        "orch_secure_communication": "True",
        "orch_endpoint": "http://127.0.0.1:11011",
        "status_endpoint": "http://127.0.0.1:11012",
        "private_key_path": str(key_path),
    }.items():
        pattern = rf"^{name}\s*=.*$"
        if re.search(pattern, section, re.MULTILINE):
            section = re.sub(pattern, f"{name} = {value}", section, flags=re.MULTILINE)
        else:
            section = section.rstrip() + f"\n{name} = {value}\n\n"
    new_content = content[: match.start()] + section + content[match.end() :]
    if new_content == content and not key_changed:
        return
    path.write_text(new_content, encoding="utf-8")
    _sync_config_to_data_path(new_content, UA_CONFIG_DATA_PATH)
    subprocess.run(["systemctl", "try-restart", "exordos-universal-agent"], check=True)


def _sync_config_to_data_path(content: str, data_path: str) -> None:
    """Write the same config content to the mirrored data path.

    Ensures config files under /var/lib/exordos/data/etc/ stay in sync
    with their /etc/ counterparts.
    """
    try:
        os.makedirs(os.path.dirname(data_path), exist_ok=True)
        with open(data_path, "w", encoding="utf-8") as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(content)
        LOG.info("Synced config to %s", data_path)
    except OSError as e:
        LOG.warning("Failed to sync config to %s: %s", data_path, e)


# The parts of the universal agent config that evolve with the core image.
# The scheduler section is fully rewritten to handle any indentation
# differences; keep both literals in sync with
# etc/exordos_universal_agent/exordos_universal_agent.conf.j2.
_UA_CAPABILITIES = [
    "em_*",
    "secret",
    "password",
    "certificate",
    "paas_lb_agent",
    "repo_proxy_installed_element",
    "border_agent",
]
# Advertised only by an installation that carries an overlay, so an upgrade
# without one changes no capability and restarts no agent.
_UA_EVPN_CAPABILITIES = [
    "evpn_port",
    "evpn_host",
    "evpn_nf",
    "bgp_rr",
]


def _ua_scheduler_section(with_overlay: bool) -> str:
    caps = _UA_CAPABILITIES + (_UA_EVPN_CAPABILITIES if with_overlay else [])
    return "[universal_agent_scheduler]\ncapabilities =\n%s\n" % ",\n".join(
        "    %s" % cap for cap in caps
    )


_UA_BORDER_DRIVER_LINE = "    BorderAgentCapabilityDriver,\n"
_UA_EVPN_DRIVER_LINE = "    EvpnAgentCapabilityDriver,\n"
_UA_OPAQUE_SECRET_DRIVER_LINE = "    OpaqueSecretCapabilityDriver,\n"

# Extra lines appended to the [SecretCapabilityDriver] section when
# migrating from [UserCapabilityDriver]. Keep in sync with
# etc/exordos_universal_agent/exordos_universal_agent.conf.j2.
_UA_SECRET_DRIVER_EXTRA_LINES = (
    "em_core_iam_users = /v1/iam/users/, password\n"
    "em_core_iam_clients = /v1/iam/clients/, secret, "
    "filter:project_id:12345678-c625-4fee-81d5-f691897b8142\n"
)


def _migrate_ua_secret_driver(content: str) -> str:
    """Migrate UserCapabilityDriver to SecretCapabilityDriver.

    Replaces the driver name in caps_drivers, renames the
    [UserCapabilityDriver] section to [SecretCapabilityDriver], and
    appends the IAM users/clients endpoint lines required by the
    secret driver. Idempotent.
    """
    if "UserCapabilityDriver" in content:
        content = content.replace(
            "    UserCapabilityDriver,\n",
            "    SecretCapabilityDriver,\n",
            1,
        )
        content = content.replace(
            "[UserCapabilityDriver]",
            "[SecretCapabilityDriver]",
            1,
        )

    if "em_core_iam_users" not in content:
        match = re.search(
            r"(\[SecretCapabilityDriver\].*?)(?=^\[|\Z)",
            content,
            flags=re.DOTALL | re.MULTILINE,
        )
        if match is not None:
            section = match.group(0)
            if not section.endswith("\n"):
                section += "\n"
            section += _UA_SECRET_DRIVER_EXTRA_LINES
            content = content[: match.start()] + section + content[match.end() :]
        else:
            LOG.warning(
                "Could not find [SecretCapabilityDriver] section to append "
                "IAM users/clients endpoints"
            )

    return content


def _ensure_ua_config_current(with_overlay: tp.Optional[bool] = False) -> None:
    """Bring the universal agent config up to date with this image.

    ec-bootstrap-templates restores /etc configs from the persisted
    copies on the data disk, so template changes shipped in a new core
    image never reach an upgraded stand on their own. Idempotently
    reapply the image-defined parts of the UA config:

    1. Rewrite the [universal_agent_scheduler] section with the current
       capabilities list. The evpn ones are in it only when this
       installation carries an overlay (`with_overlay`) -- see
       `bootstrap_defaults.overlay_is_wanted`.
    2. Ensure BorderAgentCapabilityDriver is present in caps_drivers
       ([universal_agent] holds stand-specific endpoints, so only this
       line is inserted, not the whole section).
    3. Migrate UserCapabilityDriver to SecretCapabilityDriver in
       caps_drivers and rename the [UserCapabilityDriver] section,
       appending the IAM users/clients endpoints required by the
       secret driver.

    On changes the result is synced back to the persisted copy and the
    agent services (started before ec-bootstrap runs, thus holding the
    stale config) are restarted to pick it up.
    """
    if with_overlay is None:
        LOG.warning("Overlay state is unknown; preserving universal agent config")
        return

    try:
        with open(UA_CONFIG_PATH, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        LOG.warning("Universal agent config not found: %s", UA_CONFIG_PATH)
        return

    new_content = re.sub(
        r"^\[universal_agent_scheduler\].*?(?=^\[|\Z)",
        _ua_scheduler_section(with_overlay),
        content,
        count=1,
        flags=re.DOTALL | re.MULTILINE,
    )

    if "BorderAgentCapabilityDriver" not in new_content:
        new_content = new_content.replace(
            "    LBAgentCapabilityDriver,\n",
            "    LBAgentCapabilityDriver,\n" + _UA_BORDER_DRIVER_LINE,
            1,
        )
        if "BorderAgentCapabilityDriver" not in new_content:
            LOG.warning(
                "Could not insert BorderAgentCapabilityDriver into caps_drivers in %s",
                UA_CONFIG_PATH,
            )

    if "OpaqueSecretCapabilityDriver" not in new_content:
        new_content = new_content.replace(
            "    PasswordCapabilityDriver,\n",
            _UA_OPAQUE_SECRET_DRIVER_LINE + "    PasswordCapabilityDriver,\n",
            1,
        )
        if "OpaqueSecretCapabilityDriver" not in new_content:
            LOG.warning(
                "Could not insert OpaqueSecretCapabilityDriver into caps_drivers in %s",
                UA_CONFIG_PATH,
            )

    new_content = _migrate_ua_secret_driver(new_content)

    if (
        with_overlay
        and "EvpnAgentCapabilityDriver" not in new_content
        and not os.path.exists(SDN_AGENT_CONFIG_PATH)
    ):
        new_content = new_content.replace(
            _UA_BORDER_DRIVER_LINE,
            _UA_BORDER_DRIVER_LINE + _UA_EVPN_DRIVER_LINE,
            1,
        )
        if "EvpnAgentCapabilityDriver" not in new_content:
            LOG.warning(
                "Could not insert EvpnAgentCapabilityDriver into caps_drivers in %s",
                UA_CONFIG_PATH,
            )

    if new_content == content:
        LOG.info("Universal agent config already up to date in %s", UA_CONFIG_PATH)
        return

    with open(UA_CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(new_content)
    LOG.info("Updated universal agent config %s", UA_CONFIG_PATH)
    _sync_config_to_data_path(new_content, UA_CONFIG_DATA_PATH)

    try:
        subprocess.run(
            [
                "systemctl",
                "try-restart",
                "exordos-universal-agent",
                "exordos-universal-scheduler",
            ],
            check=True,
        )
        LOG.info("Restarted universal agent services to apply the new config")
    except (OSError, subprocess.CalledProcessError) as e:
        LOG.warning("Failed to restart universal agent services: %s", e)
