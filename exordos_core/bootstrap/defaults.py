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
import typing as tp
import uuid as sys_uuid

from gcl_sdk.infra.dm import models as infra_models
from restalchemy.dm import filters as dm_filters
from restalchemy.storage import exceptions as ra_exceptions

from exordos_core.common import constants as c
from exordos_core.compute import constants as nc
from exordos_core.compute.dm import models as compute_models
from exordos_core.compute.node_set.dm import models as node_set_models
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


def add_core_set(
    spec: dict[str, tp.Any], set_active: bool = False
) -> "node_set_models.NodeSet":
    """Idempotent core set and nodes addition.

    We need to add the core set and nodes to the database
    to make the bootstrap process idempotent. When the core manifest
    is installed, the core set and nodes will be detected on the data
    plane and won't be created again.
    """
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
    try:
        node_set.insert()
    except ra_exceptions.ConflictRecords:
        LOG.info("Node set %s already exists", node_set.uuid)
        # Finish operation if the core set already exists
        return node_set

    if set_active:
        node_set.set_active()
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

    prefix = spec["stand"]["hypervisors"][0]["machine_prefix"]
    for i, node in enumerate(nodes):
        bootstrap_spec = spec["stand"]["bootstraps"][i]

        # Special naming for core nodes
        node.name = f"{prefix}{str(node.uuid)[:8]}-{node.name}"
        node.insert()

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
                    project_id=str(c.SERVICE_PROJECT_ID),
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
        return

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
        "project_id": c.SERVICE_PROJECT_ID,
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
        subnet = compute_models.Subnet.restore_from_simple_view(**subnet_data)
        subnet.network = network.uuid
        subnet.insert()
        LOG.info("Created subnet %s", subnet.uuid)


def apply_startup_db(spec: dict[str, tp.Any]) -> None:
    """Idempotent startup database configuration."""
    stand = spec.get("stand", {})
    if not stand:
        LOG.info("No `stand` section found in %s", spec)
        return

    # Apply flat network configuration
    apply_flat_network(stand)

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
                pool.insert(agent_private_key=agent_private_key)
            except ra_exceptions.ConflictRecords:
                LOG.info("Machine pool %s already exists", pool.uuid)
            else:
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
