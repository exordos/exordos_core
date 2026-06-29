#    Copyright 2026 Genesis Corporation.
#
#    All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.

import base64
import hashlib
import logging
import os
import uuid as _uuid

from restalchemy.storage.sql import migrations

LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# UUID helpers used for deterministic permission/project UUIDs
# ---------------------------------------------------------------------------

_NS_UUID_DFD0 = _uuid.UUID("dfd0c604-607f-4260-981f-374f88435ea0")
_NS_UUID_A6C4 = _uuid.UUID("a6c4c7a8-b1d2-4e8f-9a3c-1d7e4f5a9b0c")


def _u(name: str) -> str:
    return str(_uuid.uuid5(_NS_UUID_DFD0, name))


def _generate_uuid_a6c4(name):
    return str(_uuid.uuid5(_NS_UUID_A6C4, name))


def _generate_hash(secret, secret_salt, global_salt):
    raw_secret_salt = base64.b64decode(secret_salt)
    raw_global_salt = base64.b64decode(global_salt)
    hashed = hashlib.pbkdf2_hmac(
        "sha512",
        secret.encode("utf-8"),
        raw_secret_salt + raw_global_salt,
        251685,
    )
    return hashed.hex()


# ---------------------------------------------------------------------------
# Bootstrap data UUID constants
# ---------------------------------------------------------------------------

EXORDOS_CORE_ORGANIZATION_ID = "11111111-1111-1111-1111-111111111111"
EXORDOS_CORE_ORGANIZATION_NAME = "Genesis Corporation"
EXORDOS_CORE_ORGANIZATION_DESCRIPTION = (
    "The organization serves as the central platform for all services"
    " and elements developed by Genesis Corporation."
)
DEFAULT_IAM_CLIENT_UUID = "00000000-0000-0000-0000-000000000000"
NEWCOMER_ROLE_UUID = "726f6c65-0000-0000-0000-000000000001"
OWNER_ROLE_UUID = "726f6c65-0000-0000-0000-000000000002"
NEWCOMER_ROLE_NAME = "newcomer"
NEWCOMER_ROLE_DESCRIPTION = (
    "Default role for newly registered users. Provides basic system access"
    " and onboarding capabilities."
)
OWNER_ROLE_NAME = "owner"
OWNER_ROLE_DESCRIPTION = (
    "Project ownership role. Grants full administrative privileges"
    " within a specific project. Automatically assigned during project"
    " creation process."
)
IAM_PROJECT_UUID = _generate_uuid_a6c4("GenesisCore-IAM-Project")
COMPUTE_PROJECT_UUID = _u("GenesisCore-Compute-Project")
DNS_PROJECT_UUID = _u("GenesisCore-Dns-Project")

# ---------------------------------------------------------------------------
# Permission name constants and UUID mappings
# ---------------------------------------------------------------------------

USER_LIST = "iam.user.list"
USER_READ_ALL = "iam.user.read_all"
USER_WRITE_ALL = "iam.user.write_all"
USER_DELETE_ALL = "iam.user.delete_all"
USER_DELETE = "iam.user.delete"
ORG_CREATE = "iam.organization.create"
ORG_READ_ALL = "iam.organization.read_all"
ORG_WRITE_ALL = "iam.organization.write_all"
ORG_DELETE = "iam.organization.delete"
ORG_DELETE_ALL = "iam.organization.delete_all"

PERMISSION_PROJECT_LIST_ALL = "iam.project.list_all"
PERMISSION_PROJECT_READ_ALL = "iam.project.read_all"
PERMISSION_PROJECT_WRITE_ALL = "iam.project.write_all"
PERMISSION_PROJECT_DELETE_ALL = "iam.project.delete_all"
PERMISSION_PERMISSION_CREATE = "iam.permission.create"
PERMISSION_PERMISSION_READ = "iam.permission.read"
PERMISSION_PERMISSION_UPDATE = "iam.permission.update"
PERMISSION_PERMISSION_DELETE = "iam.permission.delete"
PERMISSION_PERMISSION_BINDING_CREATE = "iam.permission_binding.create"
PERMISSION_PERMISSION_BINDING_READ = "iam.permission_binding.read"
PERMISSION_PERMISSION_BINDING_UPDATE = "iam.permission_binding.update"
PERMISSION_PERMISSION_BINDING_DELETE = "iam.permission_binding.delete"
PERMISSION_ROLE_CREATE = "iam.role.create"
PERMISSION_ROLE_READ = "iam.role.read"
PERMISSION_ROLE_UPDATE = "iam.role.write"
PERMISSION_ROLE_DELETE = "iam.role.delete"
PERMISSION_ROLE_BINDING_CREATE = "iam.role_binding.create"
PERMISSION_ROLE_BINDING_READ = "iam.role_binding.read"
PERMISSION_ROLE_BINDING_UPDATE = "iam.role_binding.update"
PERMISSION_ROLE_BINDING_DELETE = "iam.role_binding.delete"
PERMISSION_IAM_CLIENT_CREATE = "iam.iam_client.create"
PERMISSION_IAM_CLIENT_READ_ALL = "iam.iam_client.read_all"
PERMISSION_IAM_CLIENT_UPDATE = "iam.iam_client.update"
PERMISSION_IAM_CLIENT_DELETE = "iam.iam_client.delete"

PERMISSION_UUIDS = {
    USER_LIST: _generate_uuid_a6c4(USER_LIST),
    USER_READ_ALL: _generate_uuid_a6c4(USER_READ_ALL),
    USER_WRITE_ALL: _generate_uuid_a6c4(USER_WRITE_ALL),
    USER_DELETE_ALL: _generate_uuid_a6c4(USER_DELETE_ALL),
    USER_DELETE: _generate_uuid_a6c4(USER_DELETE),
    ORG_CREATE: _generate_uuid_a6c4(ORG_CREATE),
    ORG_READ_ALL: _generate_uuid_a6c4(ORG_READ_ALL),
    ORG_WRITE_ALL: _generate_uuid_a6c4(ORG_WRITE_ALL),
    ORG_DELETE: _generate_uuid_a6c4(ORG_DELETE),
    ORG_DELETE_ALL: _generate_uuid_a6c4(ORG_DELETE_ALL),
    PERMISSION_PROJECT_LIST_ALL: _generate_uuid_a6c4(PERMISSION_PROJECT_LIST_ALL),
    PERMISSION_PROJECT_READ_ALL: _generate_uuid_a6c4(PERMISSION_PROJECT_READ_ALL),
    PERMISSION_PROJECT_WRITE_ALL: _generate_uuid_a6c4(PERMISSION_PROJECT_WRITE_ALL),
    PERMISSION_PROJECT_DELETE_ALL: _generate_uuid_a6c4(PERMISSION_PROJECT_DELETE_ALL),
    PERMISSION_PERMISSION_CREATE: _generate_uuid_a6c4(PERMISSION_PERMISSION_CREATE),
    PERMISSION_PERMISSION_READ: _generate_uuid_a6c4(PERMISSION_PERMISSION_READ),
    PERMISSION_PERMISSION_UPDATE: _generate_uuid_a6c4(PERMISSION_PERMISSION_UPDATE),
    PERMISSION_PERMISSION_DELETE: _generate_uuid_a6c4(PERMISSION_PERMISSION_DELETE),
    PERMISSION_PERMISSION_BINDING_CREATE: _generate_uuid_a6c4(
        PERMISSION_PERMISSION_BINDING_CREATE
    ),
    PERMISSION_PERMISSION_BINDING_READ: _generate_uuid_a6c4(
        PERMISSION_PERMISSION_BINDING_READ
    ),
    PERMISSION_PERMISSION_BINDING_UPDATE: _generate_uuid_a6c4(
        PERMISSION_PERMISSION_BINDING_UPDATE
    ),
    PERMISSION_PERMISSION_BINDING_DELETE: _generate_uuid_a6c4(
        PERMISSION_PERMISSION_BINDING_DELETE
    ),
    PERMISSION_ROLE_CREATE: _generate_uuid_a6c4(PERMISSION_ROLE_CREATE),
    PERMISSION_ROLE_READ: _generate_uuid_a6c4(PERMISSION_ROLE_READ),
    PERMISSION_ROLE_UPDATE: _generate_uuid_a6c4(PERMISSION_ROLE_UPDATE),
    PERMISSION_ROLE_DELETE: _generate_uuid_a6c4(PERMISSION_ROLE_DELETE),
    PERMISSION_ROLE_BINDING_CREATE: _generate_uuid_a6c4(PERMISSION_ROLE_BINDING_CREATE),
    PERMISSION_ROLE_BINDING_READ: _generate_uuid_a6c4(PERMISSION_ROLE_BINDING_READ),
    PERMISSION_ROLE_BINDING_UPDATE: _generate_uuid_a6c4(PERMISSION_ROLE_BINDING_UPDATE),
    PERMISSION_ROLE_BINDING_DELETE: _generate_uuid_a6c4(PERMISSION_ROLE_BINDING_DELETE),
    PERMISSION_IAM_CLIENT_CREATE: _generate_uuid_a6c4(PERMISSION_IAM_CLIENT_CREATE),
    PERMISSION_IAM_CLIENT_READ_ALL: _generate_uuid_a6c4(PERMISSION_IAM_CLIENT_READ_ALL),
    PERMISSION_IAM_CLIENT_UPDATE: _generate_uuid_a6c4(PERMISSION_IAM_CLIENT_UPDATE),
    PERMISSION_IAM_CLIENT_DELETE: _generate_uuid_a6c4(PERMISSION_IAM_CLIENT_DELETE),
}

COMPUTE_NODE_DEF_PERMISSIONS = (
    ("compute.node.read", "List and read own nodes"),
    ("compute.node.create", "Create own nodes"),
    ("compute.node.update", "Update own nodes"),
    ("compute.node.delete", "Delete own nodes"),
)

COMPUTE_NODE_SET_DEF_PERMISSIONS = (
    ("compute.node_set.read", "List and read own node sets"),
    ("compute.node_set.create", "Create own node sets"),
    ("compute.node_set.update", "Update own node sets"),
    ("compute.node_set.delete", "Delete own node sets"),
)

DNS_NODE_DEF_PERMISSIONS = (
    ("dns.domain.read", "List and read own domains"),
    ("dns.domain.create", "Create own domains"),
    ("dns.domain.update", "Update own domains"),
    ("dns.domain.delete", "Delete own domains"),
    ("dns.record.read", "List and read own records"),
    ("dns.record.create", "Create own records"),
    ("dns.record.update", "Update own records"),
    ("dns.record.delete", "Delete own records"),
)

NETWORK_LB_PERMISSIONS = (
    ("network.lb.read", "List and read load balancers"),
    ("network.lb.create", "Create load balancers"),
    ("network.lb.update", "Update load balancers"),
    ("network.lb.delete", "Delete load balancers"),
    ("network.lb_vhost.read", "List and read LB virtual hosts"),
    ("network.lb_vhost.create", "Create LB virtual hosts"),
    ("network.lb_vhost.update", "Update LB virtual hosts"),
    ("network.lb_vhost.delete", "Delete LB virtual hosts"),
    ("network.lb_vhost_route.read", "List and read LB vhost routes"),
    ("network.lb_vhost_route.create", "Create LB vhost routes"),
    ("network.lb_vhost_route.update", "Update LB vhost routes"),
    ("network.lb_vhost_route.delete", "Delete LB vhost routes"),
    ("network.lb_backendpool.read", "List and read LB backend pools"),
    ("network.lb_backendpool.create", "Create LB backend pools"),
    ("network.lb_backendpool.update", "Update LB backend pools"),
    ("network.lb_backendpool.delete", "Delete LB backend pools"),
    ("compute.node.get_private_key", "Get node(s) agent private key"),
    ("compute.node_set.get_private_key", "Get node(s) agent private key"),
    ("config.config.read", "List and read configs"),
    ("config.config.create", "Create configs"),
    ("config.config.update", "Update configs"),
    ("config.config.delete", "Delete configs"),
    ("em.service.read", "List and read services"),
    ("em.service.create", "Create services"),
    ("em.service.update", "Update services"),
    ("em.service.delete", "Delete services"),
)

SERVICE_TOKEN_PERMISSIONS = (
    ("iam.service_token.create", "Create service account tokens"),
)

IAM_USER_PERMISSIONS = (("iam.user.create", "Create IAM users"),)


def _constraint_exists(session, constraint_name):
    result = session.execute(
        f"""SELECT 1 FROM pg_catalog.pg_constraint
            WHERE conname = '{constraint_name}'""",
        None,
    )
    return result is not None and result.rowcount > 0


def _index_exists(session, index_name):
    result = session.execute(
        f"""SELECT 1 FROM pg_catalog.pg_indexes
            WHERE indexname = '{index_name}'""",
        None,
    )
    return result is not None and result.rowcount > 0


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0000-root-d34de1.py"]

    @property
    def migration_id(self):
        return "3b0440ab-0814-4ea3-8fea-cb72d3ef7d2b"

    @property
    def is_manual(self):
        return False

    # ------------------------------------------------------------------
    # Upgrade
    # ------------------------------------------------------------------

    def upgrade(self, session):
        self._upgrade_types(session)
        self._upgrade_sequences(session)
        self._upgrade_tables(session)
        self._upgrade_sequence_ownership(session)
        self._upgrade_views(session)
        self._upgrade_primary_keys(session)
        self._upgrade_unique_constraints(session)
        self._upgrade_foreign_keys(session)
        self._upgrade_indexes(session)
        self._upgrade_unique_indexes(session)
        self._upgrade_data(session)

    def downgrade(self, session):
        self._downgrade_data(session)
        self._downgrade_unique_indexes(session)
        self._downgrade_indexes(session)
        self._downgrade_foreign_keys(session)
        self._downgrade_unique_constraints(session)
        self._downgrade_primary_keys(session)
        self._downgrade_views(session)
        self._downgrade_sequence_ownership(session)
        self._downgrade_tables(session)
        self._downgrade_sequences(session)
        self._downgrade_types(session)

    # ==================================================================
    # TYPES
    # ==================================================================

    def _type_exists(self, session, type_name):
        result = session.execute(
            f"SELECT 1 FROM pg_catalog.pg_type WHERE typname = '{type_name}'",
            None,
        )
        return result is not None and result.rowcount > 0

    def _upgrade_types(self, session):
        types = [
            (
                "public.enum_agent_status",
                "CREATE TYPE public.enum_agent_status AS ENUM ("
                "'NEW', 'ACTIVE', 'ERROR', 'DISABLED')",
            ),
            (
                "public.enum_config_status",
                "CREATE TYPE public.enum_config_status AS ENUM ("
                "'NEW', 'IN_PROGRESS', 'ACTIVE', 'ERROR')",
            ),
            (
                "public.enum_secret_status",
                "CREATE TYPE public.enum_secret_status AS ENUM ("
                "'NEW', 'IN_PROGRESS', 'ACTIVE', 'ERROR')",
            ),
            (
                "public.enum_service_status",
                "CREATE TYPE public.enum_service_status AS ENUM ("
                "'NEW', 'IN_PROGRESS', 'ACTIVE', 'ERROR')",
            ),
            (
                "public.enum_service_target_status",
                "CREATE TYPE public.enum_service_target_status AS ENUM ("
                "'enabled', 'disabled')",
            ),
            (
                "public.user_type_enum",
                "CREATE TYPE public.user_type_enum AS ENUM ('user', 'service')",
            ),
        ]
        for type_name, stmt in types:
            schema_qualified = type_name.split(".")[-1]
            if not self._type_exists(session, schema_qualified):
                session.execute(stmt, None)
            else:
                LOG.warning("Type already exists, skipping: %s", type_name)

    def _downgrade_types(self, session):
        types = [
            "public.user_type_enum",
            "public.enum_service_target_status",
            "public.enum_service_status",
            "public.enum_secret_status",
            "public.enum_config_status",
            "public.enum_agent_status",
        ]
        for t in types:
            try:
                session.execute(f"DROP TYPE IF EXISTS {t} CASCADE", None)
            except Exception:
                pass

    # ==================================================================
    # SEQUENCES
    # ==================================================================

    def _upgrade_sequences(self, session):
        statements = [
            """CREATE SEQUENCE IF NOT EXISTS public.dns_domain_id_seq
                START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1""",
            """CREATE SEQUENCE IF NOT EXISTS public.dns_domainmetadata_id_seq
                AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1""",
        ]
        for stmt in statements:
            session.execute(stmt, None)

    def _downgrade_sequences(self, session):
        session.execute(
            "DROP SEQUENCE IF EXISTS public.dns_domainmetadata_id_seq CASCADE", None
        )
        session.execute(
            "DROP SEQUENCE IF EXISTS public.dns_domain_id_seq CASCADE", None
        )

    def _upgrade_sequence_ownership(self, session):
        statements = [
            """ALTER SEQUENCE public.dns_domainmetadata_id_seq
                OWNED BY public.dns_domainmetadata.id""",
        ]
        for stmt in statements:
            session.execute(stmt, None)

    def _downgrade_sequence_ownership(self, session):
        pass

    # ==================================================================
    # TABLES
    # ==================================================================

    def _upgrade_tables(self, session):
        statements = [
            # --- Compute ---
            """CREATE TABLE IF NOT EXISTS public.compute_net_interfaces (
                uuid uuid NOT NULL,
                name varchar(255) NOT NULL,
                description varchar(255) NOT NULL,
                machine uuid,
                ipv4 varchar(15) DEFAULT NULL,
                mask varchar(15) DEFAULT NULL,
                mac varchar(17) DEFAULT NULL,
                mtu integer DEFAULT 1500,
                created_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS public.compute_ports (
                uuid uuid NOT NULL,
                name varchar(255) NOT NULL,
                description varchar(255) NOT NULL,
                project_id uuid NOT NULL,
                subnet uuid,
                node uuid,
                machine uuid,
                interface varchar(32) DEFAULT NULL,
                target_ipv4 varchar(15) DEFAULT NULL,
                target_mask varchar(15) DEFAULT NULL,
                ipv4 varchar(15) DEFAULT NULL,
                mask varchar(15) DEFAULT NULL,
                mac varchar(17) DEFAULT NULL,
                status varchar(32) NOT NULL,
                created_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                source varchar(128) DEFAULT NULL,
                CONSTRAINT compute_ports_status_check CHECK (
                    status IN ('NEW', 'IN_PROGRESS', 'ACTIVE', 'ERROR')
                )
            )""",
            """CREATE TABLE IF NOT EXISTS public.machines (
                uuid uuid NOT NULL,
                project_id uuid NOT NULL,
                name varchar(255) NOT NULL,
                description varchar(255) NOT NULL,
                cores integer NOT NULL,
                ram integer NOT NULL,
                node uuid,
                machine_type varchar(2) NOT NULL,
                boot varchar(8) NOT NULL,
                pool uuid,
                firmware_uuid uuid,
                status varchar(32) NOT NULL,
                created_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                image varchar(255) DEFAULT NULL,
                block_devices jsonb DEFAULT '{}'::jsonb,
                CONSTRAINT machines_boot_check CHECK (
                    boot IN ('hd0','hd1','hd2','hd3','cdrom','network')
                ),
                CONSTRAINT machines_machine_type_check CHECK (
                    machine_type IN ('VM','HW')
                ),
                CONSTRAINT machines_status_check CHECK (
                    status IN ('NEW','SCHEDULED','IN_PROGRESS','STARTED','ACTIVE','IDLE','ERROR')
                )
            )""",
            """CREATE TABLE IF NOT EXISTS public.nodes (
                uuid uuid NOT NULL,
                project_id uuid NOT NULL,
                name varchar(255) NOT NULL,
                description varchar(255) NOT NULL,
                cores integer NOT NULL,
                ram integer NOT NULL,
                node_type varchar(2) NOT NULL,
                status varchar(32) NOT NULL,
                created_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                default_network varchar(255) DEFAULT NULL,
                node_set uuid,
                placement_policies uuid[] DEFAULT '{}'::uuid[] NOT NULL,
                disk_spec jsonb,
                hostname varchar(256) DEFAULT NULL,
                CONSTRAINT nodes_node_type_check CHECK (
                    node_type IN ('VM','HW')
                ),
                CONSTRAINT nodes_status_check CHECK (
                    status IN ('NEW','SCHEDULED','IN_PROGRESS','STARTED','ACTIVE','ERROR')
                )
            )""",
            """CREATE TABLE IF NOT EXISTS public.compute_machine_volumes (
                uuid uuid NOT NULL,
                project_id uuid NOT NULL,
                name varchar(255) NOT NULL,
                description varchar(255) NOT NULL,
                node_volume uuid,
                pool uuid,
                machine uuid,
                size integer NOT NULL,
                boot boolean DEFAULT true NOT NULL,
                index integer DEFAULT 4096 NOT NULL,
                label varchar(256),
                image varchar(256) DEFAULT NULL,
                device_type varchar(64) DEFAULT '' NOT NULL,
                status varchar(32) DEFAULT 'NEW' NOT NULL,
                created_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                CONSTRAINT compute_machine_volumes_status_check CHECK (
                    status IN ('NEW','IN_PROGRESS','ACTIVE','ERROR')
                )
            )""",
            """CREATE TABLE IF NOT EXISTS public.compute_networks (
                uuid uuid NOT NULL,
                name varchar(255) NOT NULL,
                description varchar(255) NOT NULL,
                project_id uuid NOT NULL,
                driver_spec jsonb DEFAULT '{}'::jsonb NOT NULL,
                created_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS public.compute_placement_domains (
                uuid uuid NOT NULL,
                name varchar(255) NOT NULL,
                description varchar(255) NOT NULL,
                created_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS public.compute_placement_policies (
                uuid uuid NOT NULL,
                name varchar(255) NOT NULL,
                description varchar(255) NOT NULL,
                project_id uuid NOT NULL,
                domain uuid,
                zone uuid,
                kind varchar(64) NOT NULL,
                created_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS public.compute_placement_policy_allocations (
                uuid uuid NOT NULL,
                node uuid,
                policy uuid,
                created_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS public.compute_placement_zones (
                uuid uuid NOT NULL,
                name varchar(255) NOT NULL,
                description varchar(255) NOT NULL,
                domain uuid,
                created_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS public.compute_sets (
                uuid uuid NOT NULL,
                project_id uuid NOT NULL,
                name varchar(255) NOT NULL,
                description varchar(255) NOT NULL,
                cores integer NOT NULL,
                ram integer NOT NULL,
                replicas integer NOT NULL,
                node_type varchar(2) NOT NULL,
                set_type varchar(32) NOT NULL,
                status varchar(32) NOT NULL,
                nodes jsonb NOT NULL,
                default_network jsonb NOT NULL,
                created_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                disk_spec jsonb,
                CONSTRAINT compute_sets_node_type_check CHECK (
                    node_type IN ('VM','HW')
                ),
                CONSTRAINT compute_sets_set_type_check CHECK (
                    set_type = 'SET'
                ),
                CONSTRAINT compute_sets_status_check CHECK (
                    status IN ('NEW','SCHEDULED','IN_PROGRESS','STARTED','ACTIVE','ERROR')
                )
            )""",
            """CREATE TABLE IF NOT EXISTS public.compute_subnets (
                uuid uuid NOT NULL,
                name varchar(255) NOT NULL,
                description varchar(255) NOT NULL,
                project_id uuid NOT NULL,
                network uuid,
                cidr varchar(18) NOT NULL,
                ip_range varchar(31) DEFAULT NULL,
                dhcp boolean DEFAULT true,
                dns_servers varchar(512) DEFAULT '{}' NOT NULL,
                routers varchar(512) DEFAULT '{}' NOT NULL,
                next_server varchar(256) DEFAULT '127.0.0.1',
                created_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                ip_discovery_range varchar(31) DEFAULT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS public.node_volumes (
                uuid uuid NOT NULL,
                project_id uuid NOT NULL,
                name varchar(255) NOT NULL,
                description varchar(255) NOT NULL,
                node uuid,
                size integer NOT NULL,
                boot boolean DEFAULT true NOT NULL,
                label varchar(127),
                device_type varchar(64) NOT NULL,
                created_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                status varchar(32) DEFAULT 'NEW' NOT NULL,
                index integer DEFAULT 4096,
                pool uuid,
                image varchar(256) DEFAULT NULL,
                CONSTRAINT node_volumes_status_check CHECK (
                    status IN ('NEW','IN_PROGRESS','ACTIVE','ERROR')
                )
            )""",
            # --- Config ---
            """CREATE TABLE IF NOT EXISTS public.config_configs (
                uuid uuid NOT NULL,
                name varchar(255) NOT NULL,
                description varchar(255) NOT NULL,
                project_id uuid NOT NULL,
                status public.enum_config_status DEFAULT 'NEW' NOT NULL,
                path varchar(255) NOT NULL,
                target jsonb NOT NULL,
                body jsonb NOT NULL,
                on_change jsonb NOT NULL,
                mode char(4) NOT NULL,
                owner varchar(128) NOT NULL,
                "group" varchar(128) NOT NULL,
                created_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL
            )""",
            # --- DNS ---
            """CREATE TABLE IF NOT EXISTS public.dns_domainmetadata (
                id integer NOT NULL,
                domain_id integer,
                kind varchar(32),
                content text
            )""",
            """CREATE TABLE IF NOT EXISTS public.dns_domains (
                uuid uuid NOT NULL,
                id integer DEFAULT nextval('public.dns_domain_id_seq'::regclass),
                name varchar(255) NOT NULL,
                master varchar(128) DEFAULT NULL,
                last_check integer,
                type text DEFAULT 'NATIVE' NOT NULL,
                notified_serial bigint,
                account varchar(40) DEFAULT NULL,
                options text,
                catalog text,
                project_id uuid NOT NULL,
                created_at timestamp(6) DEFAULT now() NOT NULL,
                updated_at timestamp(6) DEFAULT now() NOT NULL,
                sync_to_ecosystem boolean DEFAULT false NOT NULL,
                CONSTRAINT c_lowercase_name CHECK (name = lower(name))
            )""",
            """CREATE TABLE IF NOT EXISTS public.dns_records (
                uuid uuid NOT NULL,
                domain_id integer,
                name varchar(255) DEFAULT NULL,
                type varchar(10) DEFAULT NULL,
                content varchar(65535) DEFAULT NULL,
                ttl integer,
                prio integer,
                disabled boolean DEFAULT false,
                ordername varchar(255),
                auth boolean DEFAULT true,
                domain uuid NOT NULL,
                record jsonb NOT NULL,
                created_at timestamp(6) DEFAULT now() NOT NULL,
                updated_at timestamp(6) DEFAULT now() NOT NULL,
                project_id uuid NOT NULL,
                CONSTRAINT dns_records_name_check CHECK (name = lower(name))
            )""",
            # --- Elements ---
            """CREATE TABLE IF NOT EXISTS public.em_elements (
                uuid uuid NOT NULL,
                name varchar(255) NOT NULL,
                description varchar(255) DEFAULT '' NOT NULL,
                status varchar(20) DEFAULT 'NEW' NOT NULL,
                version varchar(64) NOT NULL,
                install_type varchar(20) DEFAULT 'MANUAL' NOT NULL,
                created_at timestamp(6) DEFAULT now() NOT NULL,
                updated_at timestamp(6) DEFAULT now() NOT NULL,
                api_version varchar(16) DEFAULT NULL,
                profile uuid,
                project_id uuid,
                requirements jsonb DEFAULT '{}'::jsonb NOT NULL,
                manifest uuid,
                CONSTRAINT em_elements_install_type_check CHECK (
                    install_type IN ('MANUAL','AUTO_AS_DEPENDENCY')
                ),
                CONSTRAINT em_elements_status_check CHECK (
                    status IN ('NEW','IN_PROGRESS','ACTIVE')
                )
            )""",
            """CREATE TABLE IF NOT EXISTS public.em_exports (
                uuid uuid NOT NULL,
                element uuid NOT NULL,
                name varchar(255) NOT NULL,
                kind varchar(20) DEFAULT 'resource' NOT NULL,
                link varchar(255) NOT NULL,
                created_at timestamp(6) DEFAULT now() NOT NULL,
                updated_at timestamp(6) DEFAULT now() NOT NULL,
                CONSTRAINT em_exports_kind_check CHECK (kind = 'resource')
            )""",
            """CREATE TABLE IF NOT EXISTS public.em_imports (
                uuid uuid NOT NULL,
                element uuid NOT NULL,
                from_element uuid NOT NULL,
                from_resource uuid NOT NULL,
                name varchar(255) NOT NULL,
                kind varchar(20) DEFAULT 'resource' NOT NULL,
                created_at timestamp(6) DEFAULT now() NOT NULL,
                updated_at timestamp(6) DEFAULT now() NOT NULL,
                CONSTRAINT em_imports_kind_check CHECK (kind = 'resource')
            )""",
            """CREATE TABLE IF NOT EXISTS public.em_resources (
                uuid uuid NOT NULL,
                name varchar(255) NOT NULL,
                element uuid NOT NULL,
                status varchar(20) DEFAULT 'NEW' NOT NULL,
                resource_link_prefix varchar(256) NOT NULL,
                value jsonb DEFAULT '{}'::jsonb NOT NULL,
                target_resource uuid,
                actual_resource uuid,
                full_hash varchar(256) DEFAULT '' NOT NULL,
                created_at timestamp(6) DEFAULT now() NOT NULL,
                updated_at timestamp(6) DEFAULT now() NOT NULL,
                CONSTRAINT em_resources_status_check CHECK (
                    status IN ('NEW','IN_PROGRESS','ACTIVE')
                )
            )""",
            """CREATE TABLE IF NOT EXISTS public.em_manifests (
                uuid uuid NOT NULL,
                name varchar(255) NOT NULL,
                description varchar(255) DEFAULT '' NOT NULL,
                status varchar(20) DEFAULT 'NEW' NOT NULL,
                version varchar(64) NOT NULL,
                schema_version integer DEFAULT 1 NOT NULL,
                project_id uuid NOT NULL,
                requirements jsonb DEFAULT '{}'::jsonb NOT NULL,
                resources jsonb DEFAULT '{}'::jsonb NOT NULL,
                created_at timestamp(6) DEFAULT now() NOT NULL,
                updated_at timestamp(6) DEFAULT now() NOT NULL,
                api_version varchar(16) DEFAULT NULL,
                exports jsonb DEFAULT '{}'::jsonb NOT NULL,
                imports jsonb DEFAULT '{}'::jsonb NOT NULL,
                openapi_spec text,
                CONSTRAINT em_manifests_status_check CHECK (status = 'ACTIVE')
            )""",
            """CREATE TABLE IF NOT EXISTS public.em_services (
                uuid uuid NOT NULL,
                name varchar(255) NOT NULL,
                description varchar(255) NOT NULL,
                project_id uuid NOT NULL,
                status public.enum_service_status DEFAULT 'NEW' NOT NULL,
                target_status public.enum_service_target_status DEFAULT 'enabled' NOT NULL,
                path varchar(255) NOT NULL,
                target jsonb NOT NULL,
                service_type jsonb NOT NULL,
                before jsonb[],
                after jsonb[],
                "user" varchar(255) NOT NULL,
                "group" varchar(255),
                created_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL
            )""",
            # --- Audit / Events ---
            """CREATE TABLE IF NOT EXISTS public.gcl_sdk_audit_logs (
                uuid uuid NOT NULL,
                object_uuid uuid NOT NULL,
                object_type varchar(64) NOT NULL,
                user_uuid uuid,
                action varchar(64) NOT NULL,
                created_at timestamp(6) DEFAULT now() NOT NULL,
                updated_at timestamp(6) DEFAULT now() NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS public.gcl_sdk_events (
                uuid uuid NOT NULL,
                status varchar(20) DEFAULT 'NEW' NOT NULL,
                event_type jsonb NOT NULL,
                event_data jsonb NOT NULL,
                created_at timestamp(6) DEFAULT now() NOT NULL,
                updated_at timestamp(6) DEFAULT now() NOT NULL,
                CONSTRAINT gcl_sdk_events_status_check CHECK (
                    status IN ('NEW','IN_PROGRESS','ERROR','ACTIVE')
                )
            )""",
            # --- IAM ---
            """CREATE TABLE IF NOT EXISTS public.iam_binding_permissions (
                uuid uuid NOT NULL,
                status varchar(20) DEFAULT 'ACTIVE' NOT NULL,
                project_id uuid,
                role uuid NOT NULL,
                permission uuid NOT NULL,
                description varchar(256) DEFAULT '',
                created_at timestamp(6) DEFAULT now() NOT NULL,
                updated_at timestamp(6) DEFAULT now() NOT NULL,
                CONSTRAINT iam_binding_permissions_status_check CHECK (status = 'ACTIVE')
            )""",
            """CREATE TABLE IF NOT EXISTS public.iam_binding_roles (
                uuid uuid NOT NULL,
                status varchar(20) DEFAULT 'ACTIVE' NOT NULL,
                "user" uuid NOT NULL,
                role uuid NOT NULL,
                project uuid,
                description varchar(256) DEFAULT '',
                created_at timestamp(6) DEFAULT now() NOT NULL,
                updated_at timestamp(6) DEFAULT now() NOT NULL,
                CONSTRAINT iam_binding_roles_status_check CHECK (status = 'ACTIVE')
            )""",
            """CREATE TABLE IF NOT EXISTS public.iam_clients (
                uuid uuid NOT NULL,
                status varchar(20) DEFAULT 'ACTIVE' NOT NULL,
                name varchar(256) NOT NULL,
                project_id uuid,
                description varchar(256) DEFAULT '',
                client_id varchar(64) NOT NULL,
                secret_hash char(128) NOT NULL,
                salt char(24) NOT NULL,
                created_at timestamp(6) DEFAULT now() NOT NULL,
                updated_at timestamp(6) DEFAULT now() NOT NULL,
                signature_algorithm jsonb DEFAULT '{"kind": "HS256", "secret_uuid": "00000000-0000-0000-0000-000000000001", "previous_secret_uuid": null}'::jsonb NOT NULL,
                CONSTRAINT iam_clients_status_check CHECK (status = 'ACTIVE')
            )""",
            """CREATE TABLE IF NOT EXISTS public.iam_idp (
                uuid uuid NOT NULL,
                status varchar(20) DEFAULT 'ACTIVE' NOT NULL,
                name varchar(256) NOT NULL,
                project_id uuid,
                description varchar(256) DEFAULT '',
                scope varchar(64) DEFAULT 'openid',
                created_at timestamp(6) DEFAULT now() NOT NULL,
                updated_at timestamp(6) DEFAULT now() NOT NULL,
                iam_client uuid,
                nonce_required boolean DEFAULT true NOT NULL,
                callback jsonb DEFAULT '{"kind": "callback_uri", "callback": ""}'::jsonb NOT NULL,
                CONSTRAINT iam_idp_status_check CHECK (status = 'ACTIVE')
            )""",
            """CREATE TABLE IF NOT EXISTS public.iam_idp_authorization_info (
                uuid uuid NOT NULL,
                idp uuid NOT NULL,
                state varchar(256) NOT NULL,
                response_type varchar(20) DEFAULT 'code' NOT NULL,
                nonce varchar(256) NOT NULL,
                scope varchar(256) NOT NULL,
                expiration_time_at timestamp(6) NOT NULL,
                token uuid,
                code uuid NOT NULL,
                created_at timestamp(6) DEFAULT now() NOT NULL,
                updated_at timestamp(6) DEFAULT now() NOT NULL,
                redirect_uri varchar(256) DEFAULT '' NOT NULL,
                CONSTRAINT iam_idp_authorization_info_response_type_check CHECK (response_type = 'code')
            )""",
            """CREATE TABLE IF NOT EXISTS public.iam_organization_members (
                uuid uuid NOT NULL,
                organization uuid NOT NULL,
                "user" uuid NOT NULL,
                role varchar(20) DEFAULT 'MEMBER' NOT NULL,
                created_at timestamp(6) DEFAULT now() NOT NULL,
                updated_at timestamp(6) DEFAULT now() NOT NULL,
                CONSTRAINT iam_organization_members_role_check CHECK (
                    role IN ('OWNER','MEMBER')
                )
            )""",
            """CREATE TABLE IF NOT EXISTS public.iam_organizations (
                uuid uuid NOT NULL,
                status varchar(20) DEFAULT 'ACTIVE' NOT NULL,
                name varchar(128) NOT NULL,
                description varchar(256) DEFAULT '',
                created_at timestamp(6) DEFAULT now() NOT NULL,
                updated_at timestamp(6) DEFAULT now() NOT NULL,
                info varchar(2048) DEFAULT '{}',
                CONSTRAINT iam_organizations_status_check CHECK (status = 'ACTIVE')
            )""",
            """CREATE TABLE IF NOT EXISTS public.iam_permissions (
                uuid uuid NOT NULL,
                status varchar(20) DEFAULT 'ACTIVE' NOT NULL,
                name varchar(256) NOT NULL,
                description varchar(256) DEFAULT '',
                created_at timestamp(6) DEFAULT now() NOT NULL,
                updated_at timestamp(6) DEFAULT now() NOT NULL,
                CONSTRAINT iam_permissions_status_check CHECK (status = 'ACTIVE')
            )""",
            """CREATE TABLE IF NOT EXISTS public.iam_users (
                uuid uuid NOT NULL,
                status varchar(20) DEFAULT 'ACTIVE' NOT NULL,
                name varchar(256) NOT NULL,
                description varchar(256) NOT NULL,
                first_name varchar(128),
                last_name varchar(128),
                email varchar(128) NOT NULL,
                secret_hash char(128) NOT NULL,
                salt char(24) NOT NULL,
                otp_secret varchar(128) DEFAULT '',
                otp_enabled boolean DEFAULT false,
                created_at timestamp(6) DEFAULT now() NOT NULL,
                updated_at timestamp(6) DEFAULT now() NOT NULL,
                surname varchar(128) DEFAULT '' NOT NULL,
                phone varchar(15),
                email_verified boolean DEFAULT false NOT NULL,
                confirmation_code uuid,
                confirmation_code_made_at timestamp,
                user_source jsonb DEFAULT '{"kind": "IAM"}'::jsonb NOT NULL,
                custom_props jsonb,
                type public.user_type_enum DEFAULT 'user' NOT NULL,
                CONSTRAINT iam_users_status_check CHECK (status = 'ACTIVE')
            )""",
            """CREATE TABLE IF NOT EXISTS public.iam_projects (
                uuid uuid NOT NULL,
                status varchar(20) DEFAULT 'ACTIVE' NOT NULL,
                name varchar(128) NOT NULL,
                description varchar(256) DEFAULT '',
                organization uuid NOT NULL,
                created_at timestamp(6) DEFAULT now() NOT NULL,
                updated_at timestamp(6) DEFAULT now() NOT NULL,
                CONSTRAINT iam_projects_status_check CHECK (
                    status IN ('NEW','IN_PROGRESS','ACTIVE','DELETING')
                )
            )""",
            """CREATE TABLE IF NOT EXISTS public.iam_roles (
                uuid uuid NOT NULL,
                status varchar(20) DEFAULT 'ACTIVE' NOT NULL,
                name varchar(128) NOT NULL,
                description varchar(256) DEFAULT '',
                project_id uuid,
                created_at timestamp(6) DEFAULT now() NOT NULL,
                updated_at timestamp(6) DEFAULT now() NOT NULL,
                CONSTRAINT iam_roles_status_check CHECK (status = 'ACTIVE')
            )""",
            """CREATE TABLE IF NOT EXISTS public.iam_tokens (
                uuid uuid NOT NULL,
                "user" uuid NOT NULL,
                project uuid,
                expiration_at timestamp(6) NOT NULL,
                refresh_token_uuid uuid NOT NULL,
                refresh_expiration_at timestamp(6) NOT NULL,
                issuer varchar(256) DEFAULT NULL,
                audience varchar(256) DEFAULT 'account',
                typ varchar(64) DEFAULT 'Bearer',
                scope varchar(128) NOT NULL,
                created_at timestamp(6) DEFAULT now() NOT NULL,
                updated_at timestamp(6) DEFAULT now() NOT NULL,
                expiration_delta double precision DEFAULT 900.0 NOT NULL,
                refresh_expiration_delta double precision DEFAULT 86400.0 NOT NULL,
                nonce varchar(256) DEFAULT NULL,
                iam_client uuid NOT NULL
            )""",
            # --- Machine pools ---
            """CREATE TABLE IF NOT EXISTS public.machine_pools (
                uuid uuid NOT NULL,
                name varchar(255) NOT NULL,
                description varchar(255) NOT NULL,
                driver_spec jsonb DEFAULT '{}'::jsonb NOT NULL,
                machine_type varchar(2) NOT NULL,
                status varchar(32) NOT NULL,
                avail_cores integer DEFAULT 0 NOT NULL,
                avail_ram integer DEFAULT 0 NOT NULL,
                all_cores integer DEFAULT 0 NOT NULL,
                all_ram integer DEFAULT 0 NOT NULL,
                storage_pools jsonb[] DEFAULT '{}'::jsonb[],
                builder uuid,
                agent uuid,
                cores_ratio double precision DEFAULT 1.0 NOT NULL,
                ram_ratio double precision DEFAULT 1.0 NOT NULL,
                created_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                CONSTRAINT machine_pools_machine_type_check CHECK (
                    machine_type IN ('VM','HW')
                ),
                CONSTRAINT machine_pools_status_check CHECK (
                    status IN ('ACTIVE','DISABLED','MAINTENANCE','IN_PROGRESS')
                )
            )""",
            # --- Builders ---
            """CREATE TABLE IF NOT EXISTS public.n_builders (
                uuid uuid NOT NULL,
                status varchar(32) NOT NULL,
                created_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                CONSTRAINT n_builders_status_check CHECK (
                    status IN ('ACTIVE','DISABLED')
                )
            )""",
            """CREATE TABLE IF NOT EXISTS public.n_machine_pool_reservations (
                uuid uuid NOT NULL,
                cores integer NOT NULL,
                ram integer NOT NULL,
                pool uuid,
                machine uuid,
                created_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL
            )""",
            # --- Network LB ---
            """CREATE TABLE IF NOT EXISTS public.net_lb (
                uuid uuid NOT NULL,
                name varchar(255) NOT NULL,
                description text,
                project_id uuid NOT NULL,
                status varchar(64) DEFAULT 'NEW' NOT NULL,
                ipsv4 varchar(15)[],
                type jsonb NOT NULL,
                created_at timestamp NOT NULL,
                updated_at timestamp NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS public.net_lb_backendpools (
                uuid uuid NOT NULL,
                name varchar(64) NOT NULL,
                status varchar(64) DEFAULT 'NEW' NOT NULL,
                description text,
                project_id uuid NOT NULL,
                created_at timestamp NOT NULL,
                updated_at timestamp NOT NULL,
                endpoints jsonb[] NOT NULL,
                balance varchar(32) NOT NULL,
                parent uuid NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS public.net_lb_vhosts (
                uuid uuid NOT NULL,
                name varchar(64) NOT NULL,
                enabled boolean DEFAULT true NOT NULL,
                status varchar(64) DEFAULT 'NEW' NOT NULL,
                description text,
                project_id uuid NOT NULL,
                created_at timestamp NOT NULL,
                updated_at timestamp NOT NULL,
                protocol varchar(10) NOT NULL,
                port integer NOT NULL,
                domains varchar(255)[],
                cert jsonb,
                parent uuid NOT NULL,
                external_sources jsonb[] DEFAULT '{}'::jsonb[] NOT NULL,
                proxy_protocol_from varchar(18)
            )""",
            """CREATE TABLE IF NOT EXISTS public.net_lb_vhosts_routes (
                uuid uuid NOT NULL,
                name varchar(64) NOT NULL,
                enabled boolean DEFAULT true NOT NULL,
                status varchar(64) DEFAULT 'NEW' NOT NULL,
                description text,
                project_id uuid NOT NULL,
                created_at timestamp NOT NULL,
                updated_at timestamp NOT NULL,
                condition jsonb,
                parent uuid NOT NULL
            )""",
            # --- Secrets ---
            """CREATE TABLE IF NOT EXISTS public.secret_certificates (
                uuid uuid NOT NULL,
                name varchar(255) NOT NULL,
                description varchar(255) NOT NULL,
                project_id uuid NOT NULL,
                status varchar(32) NOT NULL,
                constructor jsonb NOT NULL,
                method jsonb NOT NULL,
                email varchar(254) NOT NULL,
                domains varchar(1024) NOT NULL,
                key text,
                cert text,
                expiration_at timestamp,
                expiration_threshold integer NOT NULL,
                overcome_threshold boolean DEFAULT false,
                created_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                CONSTRAINT secret_certificates_status_check CHECK (
                    status IN ('NEW','IN_PROGRESS','ACTIVE','ERROR')
                )
            )""",
            """CREATE TABLE IF NOT EXISTS public.secret_passwords (
                uuid uuid NOT NULL,
                name varchar(255) NOT NULL,
                description varchar(255) NOT NULL,
                project_id uuid NOT NULL,
                status public.enum_secret_status DEFAULT 'NEW' NOT NULL,
                constructor jsonb NOT NULL,
                value varchar(512) DEFAULT NULL,
                method varchar(64) NOT NULL,
                created_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                default_length integer DEFAULT 32 NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS public.secret_rsa_keys (
                uuid uuid NOT NULL,
                name varchar(255) NOT NULL,
                description varchar(255) NOT NULL,
                project_id uuid NOT NULL,
                status varchar(32) NOT NULL,
                constructor jsonb NOT NULL,
                private_key text NOT NULL,
                public_key text NOT NULL,
                bitness integer DEFAULT 2048 NOT NULL,
                created_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                CONSTRAINT secret_rsa_keys_bitness_check CHECK (
                    bitness IN (2048, 3072, 4096)
                ),
                CONSTRAINT secret_rsa_keys_status_check CHECK (
                    status IN ('NEW','IN_PROGRESS','ACTIVE','ERROR')
                )
            )""",
            """CREATE TABLE IF NOT EXISTS public.secret_ssh_keys (
                uuid uuid NOT NULL,
                name varchar(255) NOT NULL,
                description varchar(255) NOT NULL,
                project_id uuid NOT NULL,
                status varchar(32) NOT NULL,
                constructor jsonb NOT NULL,
                target jsonb NOT NULL,
                "user" varchar(64) NOT NULL,
                authorized_keys varchar(256) NOT NULL,
                target_public_key text,
                created_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                CONSTRAINT secret_ssh_keys_status_check CHECK (
                    status IN ('NEW','IN_PROGRESS','ACTIVE','ERROR')
                )
            )""",
            # --- Security ---
            """CREATE TABLE IF NOT EXISTS public.security_rules (
                uuid uuid NOT NULL,
                name varchar(255) NOT NULL,
                description varchar(255) DEFAULT '' NOT NULL,
                project_id uuid,
                condition jsonb NOT NULL,
                action jsonb NOT NULL,
                operator varchar(8) DEFAULT 'OR' NOT NULL,
                status varchar(20) DEFAULT 'ACTIVE' NOT NULL,
                created_at timestamp(6) DEFAULT now() NOT NULL,
                updated_at timestamp(6) DEFAULT now() NOT NULL,
                CONSTRAINT security_rules_operator_check CHECK (
                    operator IN ('OR','AND')
                ),
                CONSTRAINT security_rules_status_check CHECK (status = 'ACTIVE'),
                CONSTRAINT security_rules_verifier_not_null CHECK (action IS NOT NULL)
            )""",
            # --- Storage ---
            """CREATE TABLE IF NOT EXISTS public.storage_certs (
                uuid uuid NOT NULL,
                status varchar(32) NOT NULL,
                pkey varchar(10240) NOT NULL,
                fullchain varchar(10240) NOT NULL,
                csr varchar(10240) NOT NULL,
                expiration_at timestamp NOT NULL,
                meta jsonb NOT NULL,
                created_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                CONSTRAINT storage_certs_status_check CHECK (
                    status IN ('NEW','IN_PROGRESS','ACTIVE','ERROR')
                )
            )""",
            """CREATE TABLE IF NOT EXISTS public.storage_passwords (
                uuid uuid NOT NULL,
                status public.enum_secret_status DEFAULT 'NEW' NOT NULL,
                value varchar(512) NOT NULL,
                meta jsonb NOT NULL,
                created_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL
            )""",
            # --- VS ---
            """CREATE TABLE IF NOT EXISTS public.vs_profiles (
                uuid uuid NOT NULL,
                name varchar(255) NOT NULL,
                description varchar(255) NOT NULL,
                project_id uuid NOT NULL,
                status varchar(32) NOT NULL,
                profile_type varchar(32) NOT NULL,
                active boolean DEFAULT false,
                created_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                CONSTRAINT vs_profiles_profile_type_check CHECK (
                    profile_type IN ('GLOBAL','ELEMENT')
                ),
                CONSTRAINT vs_profiles_status_check CHECK (
                    status IN ('NEW','IN_PROGRESS','ACTIVE','ERROR')
                )
            )""",
            """CREATE TABLE IF NOT EXISTS public.vs_values (
                uuid uuid NOT NULL,
                name varchar(255) NOT NULL,
                description varchar(255) NOT NULL,
                project_id uuid NOT NULL,
                status varchar(32) NOT NULL,
                value jsonb,
                read_only boolean DEFAULT false,
                manual_selected boolean DEFAULT false,
                variable uuid,
                created_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                CONSTRAINT vs_values_status_check CHECK (
                    status IN ('NEW','IN_PROGRESS','ACTIVE','ERROR')
                )
            )""",
            """CREATE TABLE IF NOT EXISTS public.vs_variables (
                uuid uuid NOT NULL,
                name varchar(255) NOT NULL,
                description varchar(255) NOT NULL,
                project_id uuid NOT NULL,
                status varchar(32) NOT NULL,
                setter jsonb NOT NULL,
                value jsonb,
                created_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
                CONSTRAINT vs_variables_status_check CHECK (
                    status IN ('NEW','IN_PROGRESS','ACTIVE','ERROR')
                )
            )""",
        ]
        for stmt in statements:
            session.execute(stmt, None)

        # Set default for dns_domainmetadata.id after table creation
        session.execute(
            """ALTER TABLE ONLY public.dns_domainmetadata
               ALTER COLUMN id
               SET DEFAULT nextval('public.dns_domainmetadata_id_seq'::regclass)""",
            None,
        )

    def _downgrade_tables(self, session):
        tables = [
            "vs_variables",
            "vs_values",
            "vs_profiles",
            "storage_passwords",
            "storage_certs",
            "security_rules",
            "secret_ssh_keys",
            "secret_rsa_keys",
            "secret_passwords",
            "secret_certificates",
            "net_lb_vhosts_routes",
            "net_lb_vhosts",
            "net_lb_backendpools",
            "net_lb",
            "n_machine_pool_reservations",
            "n_builders",
            "machine_pools",
            "iam_tokens",
            "iam_roles",
            "iam_projects",
            "iam_users",
            "iam_permissions",
            "iam_organizations",
            "iam_organization_members",
            "iam_idp_authorization_info",
            "iam_idp",
            "iam_clients",
            "iam_binding_roles",
            "iam_binding_permissions",
            "gcl_sdk_events",
            "gcl_sdk_audit_logs",
            "em_services",
            "em_resources",
            "em_manifests",
            "em_imports",
            "em_exports",
            "em_elements",
            "dns_records",
            "dns_domains",
            "dns_domainmetadata",
            "config_configs",
            "compute_machine_volumes",
            "node_volumes",
            "compute_subnets",
            "compute_sets",
            "compute_ports",
            "compute_placement_zones",
            "compute_placement_policy_allocations",
            "compute_placement_policies",
            "compute_placement_domains",
            "compute_networks",
            "compute_net_interfaces",
            "machines",
            "nodes",
        ]
        for table in tables:
            self._delete_table_if_exists(session, table)

    # ==================================================================
    # VIEWS
    # ==================================================================

    def _upgrade_views(self, session):
        statements = [
            """CREATE OR REPLACE VIEW public.compute_hw_nodes_without_ports AS
             SELECT n.uuid,
                n.uuid AS node,
                m.uuid AS machine,
                ni.uuid AS iface
               FROM public.nodes n
               LEFT JOIN public.machines m ON n.uuid = m.node
               LEFT JOIN public.compute_net_interfaces ni ON ni.machine = m.uuid
               LEFT JOIN public.compute_ports p ON p.node = n.uuid
              WHERE n.node_type = 'HW'
                AND m.uuid IS NOT NULL
                AND ni.ipv4 IS NOT NULL
                AND p.uuid IS NULL""",
            """CREATE OR REPLACE VIEW public.compute_nodes_without_ports AS
             SELECT n.uuid, n.project_id, n.name, n.description,
                    n.cores, n.ram, n.node_type, n.status,
                    n.created_at, n.updated_at, n.default_network,
                    n.node_set, n.placement_policies, n.disk_spec, n.hostname
               FROM public.nodes n
               LEFT JOIN public.compute_ports p ON n.uuid = p.node
              WHERE p.uuid IS NULL""",
            """CREATE OR REPLACE VIEW public.compute_unscheduled_volumes AS
             SELECT nv.uuid, nv.uuid AS volume
               FROM public.node_volumes nv
               LEFT JOIN public.compute_machine_volumes cmv
                    ON nv.uuid = cmv.node_volume
              WHERE cmv.uuid IS NULL""",
            """CREATE OR REPLACE VIEW public.domainmetadata AS
             SELECT id, domain_id, kind, content
               FROM public.dns_domainmetadata""",
            """CREATE OR REPLACE VIEW public.domains AS
             SELECT id, name, master, last_check, type,
                    notified_serial, account, options, catalog
               FROM public.dns_domains""",
            """CREATE OR REPLACE VIEW public.em_incorrect_resource_statuses_view AS
             SELECT er.uuid, er.status AS current_status, uar.status AS actual_status
               FROM public.em_resources er
               LEFT JOIN (
                   SELECT uuid, status
                     FROM public.ua_actual_resources
                    WHERE kind LIKE 'em_%%'
               ) uar ON er.uuid = uar.uuid
              WHERE er.status <> uar.status""",
            """CREATE OR REPLACE VIEW public.em_incorrect_statuses_view AS
             WITH tmp AS (
                 SELECT element,
                        bool_or(status = 'IN_PROGRESS') AS has_in_progress,
                        bool_and(status = 'ACTIVE') AS all_active,
                        bool_and(status = 'NEW') AS all_new,
                        count(*) AS resources_count
                   FROM public.em_resources
                  GROUP BY element
             ), eis AS (
                 SELECT e.uuid, e.name, e.status AS api_status,
                        CASE
                            WHEN tmp.resources_count IS NULL THEN 'ACTIVE'
                            WHEN tmp.has_in_progress THEN 'IN_PROGRESS'
                            WHEN tmp.all_active THEN 'ACTIVE'
                            WHEN tmp.all_new THEN 'NEW'
                            ELSE 'IN_PROGRESS'
                        END AS actual_status
                   FROM public.em_elements e
                   LEFT JOIN tmp ON e.uuid = tmp.element
             )
             SELECT uuid, name, api_status, actual_status
               FROM eis
              WHERE api_status <> actual_status""",
            """CREATE OR REPLACE VIEW public.em_outdated_resources_view AS
             SELECT COALESCE(er.uuid, utr.uuid) AS uuid,
                    er.uuid AS em_resource,
                    utr.res_uuid AS target_resource
               FROM public.em_resources er
               FULL JOIN (
                   SELECT uuid, res_uuid, updated_at, tracked_at
                     FROM public.ua_target_resources
                    WHERE kind LIKE 'em_%%'
               ) utr ON er.uuid = utr.uuid
              WHERE er.uuid IS NULL
                 OR utr.uuid IS NULL
                 OR er.updated_at <> utr.tracked_at""",
            """CREATE OR REPLACE VIEW public.iam_permissions_fast_view AS
             SELECT t1.uuid, t1.uuid AS permission,
                    t4.uuid AS "user", t3.uuid AS role, t3.project
               FROM public.iam_permissions t1
               LEFT JOIN public.iam_binding_permissions t2
                    ON t2.permission = t1.uuid
               LEFT JOIN public.iam_binding_roles t3
                    ON t3.role = t2.role
               LEFT JOIN public.iam_users t4
                    ON t4.uuid = t3.\"user\"""",
            """CREATE OR REPLACE VIEW public.netboots AS
             SELECT firmware_uuid AS uuid, boot
               FROM public.machines""",
            """CREATE OR REPLACE VIEW public.records AS
             SELECT domain_id, name, type, content, ttl,
                    prio, disabled, ordername, auth
               FROM public.dns_records""",
            """CREATE OR REPLACE VIEW public.ua_node_verifiers_view AS
             SELECT uuid FROM public.ua_node_encryption_keys""",
            """CREATE OR REPLACE VIEW public.unscheduled_nodes AS
             SELECT n.uuid, n.uuid AS node
               FROM public.nodes n
               LEFT JOIN public.machines m ON n.uuid = m.node
              WHERE m.uuid IS NULL""",
        ]
        for stmt in statements:
            session.execute(stmt, None)

    def _downgrade_views(self, session):
        views = [
            "unscheduled_nodes",
            "records",
            "netboots",
            "iam_permissions_fast_view",
            "em_outdated_resources_view",
            "em_incorrect_statuses_view",
            "em_incorrect_resource_statuses_view",
            "domains",
            "domainmetadata",
            "compute_unscheduled_volumes",
            "compute_nodes_without_ports",
            "compute_hw_nodes_without_ports",
        ]
        for view in views:
            self._delete_view_if_exists(session, view)

    # ==================================================================
    # PRIMARY KEYS
    # ==================================================================

    def _upgrade_primary_keys(self, session):
        pks = [
            ("compute_machine_volumes_pkey", "public.compute_machine_volumes", "uuid"),
            ("compute_net_interfaces_pkey", "public.compute_net_interfaces", "uuid"),
            ("compute_networks_pkey", "public.compute_networks", "uuid"),
            (
                "compute_placement_domains_pkey",
                "public.compute_placement_domains",
                "uuid",
            ),
            (
                "compute_placement_policies_pkey",
                "public.compute_placement_policies",
                "uuid",
            ),
            (
                "compute_placement_policy_allocations_pkey",
                "public.compute_placement_policy_allocations",
                "uuid",
            ),
            ("compute_placement_zones_pkey", "public.compute_placement_zones", "uuid"),
            ("compute_ports_pkey", "public.compute_ports", "uuid"),
            ("compute_sets_pkey", "public.compute_sets", "uuid"),
            ("compute_subnets_pkey", "public.compute_subnets", "uuid"),
            ("config_configs_pkey", "public.config_configs", "uuid"),
            ("dns_domainmetadata_pkey", "public.dns_domainmetadata", "id"),
            ("dns_domains_pkey", "public.dns_domains", "uuid"),
            ("dns_records_pkey", "public.dns_records", "uuid"),
            ("em_elements_pkey", "public.em_elements", "uuid"),
            ("em_exports_pkey", "public.em_exports", "uuid"),
            ("em_imports_pkey", "public.em_imports", "uuid"),
            ("em_manifests_pkey", "public.em_manifests", "uuid"),
            ("em_resources_pkey", "public.em_resources", "uuid"),
            ("em_services_pkey", "public.em_services", "uuid"),
            ("gcl_sdk_audit_logs_pkey", "public.gcl_sdk_audit_logs", "uuid"),
            ("gcl_sdk_events_pkey", "public.gcl_sdk_events", "uuid"),
            ("iam_binding_permissions_pkey", "public.iam_binding_permissions", "uuid"),
            ("iam_binding_roles_pkey", "public.iam_binding_roles", "uuid"),
            ("iam_clients_pkey", "public.iam_clients", "uuid"),
            (
                "iam_idp_authorization_info_pkey",
                "public.iam_idp_authorization_info",
                "uuid",
            ),
            ("iam_idp_pkey", "public.iam_idp", "uuid"),
            (
                "iam_organization_members_pkey",
                "public.iam_organization_members",
                "uuid",
            ),
            ("iam_organizations_pkey", "public.iam_organizations", "uuid"),
            ("iam_permissions_pkey", "public.iam_permissions", "uuid"),
            ("iam_projects_pkey", "public.iam_projects", "uuid"),
            ("iam_roles_pkey", "public.iam_roles", "uuid"),
            ("iam_tokens_pkey", "public.iam_tokens", "uuid"),
            ("iam_users_pkey", "public.iam_users", "uuid"),
            ("machine_pools_pkey", "public.machine_pools", "uuid"),
            ("machines_pkey", "public.machines", "uuid"),
            ("n_builders_pkey", "public.n_builders", "uuid"),
            (
                "n_machine_pool_reservations_pkey",
                "public.n_machine_pool_reservations",
                "uuid",
            ),
            ("net_lb_backendpools_pkey", "public.net_lb_backendpools", "uuid"),
            ("net_lb_pkey", "public.net_lb", "uuid"),
            ("net_lb_vhosts_pkey", "public.net_lb_vhosts", "uuid"),
            ("net_lb_vhosts_routes_pkey", "public.net_lb_vhosts_routes", "uuid"),
            ("node_volumes_pkey", "public.node_volumes", "uuid"),
            ("nodes_pkey", "public.nodes", "uuid"),
            ("secret_certificates_pkey", "public.secret_certificates", "uuid"),
            ("secret_passwords_pkey", "public.secret_passwords", "uuid"),
            ("secret_rsa_keys_pkey", "public.secret_rsa_keys", "uuid"),
            ("secret_ssh_keys_pkey", "public.secret_ssh_keys", "uuid"),
            ("security_rules_pkey", "public.security_rules", "uuid"),
            ("storage_certs_pkey", "public.storage_certs", "uuid"),
            ("storage_passwords_pkey", "public.storage_passwords", "uuid"),
            ("vs_profiles_pkey", "public.vs_profiles", "uuid"),
            ("vs_values_pkey", "public.vs_values", "uuid"),
            ("vs_variables_pkey", "public.vs_variables", "uuid"),
        ]
        for conname, table, column in pks:
            if not _constraint_exists(session, conname):
                session.execute(
                    f"ALTER TABLE ONLY {table} ADD CONSTRAINT {conname} PRIMARY KEY ({column})",
                    None,
                )

    def _downgrade_primary_keys(self, session):
        pk_names = [
            ("compute_machine_volumes_pkey", "public.compute_machine_volumes"),
            ("compute_net_interfaces_pkey", "public.compute_net_interfaces"),
            ("compute_networks_pkey", "public.compute_networks"),
            ("compute_placement_domains_pkey", "public.compute_placement_domains"),
            ("compute_placement_policies_pkey", "public.compute_placement_policies"),
            (
                "compute_placement_policy_allocations_pkey",
                "public.compute_placement_policy_allocations",
            ),
            ("compute_placement_zones_pkey", "public.compute_placement_zones"),
            ("compute_ports_pkey", "public.compute_ports"),
            ("compute_sets_pkey", "public.compute_sets"),
            ("compute_subnets_pkey", "public.compute_subnets"),
            ("config_configs_pkey", "public.config_configs"),
            ("dns_domainmetadata_pkey", "public.dns_domainmetadata"),
            ("dns_domains_pkey", "public.dns_domains"),
            ("dns_records_pkey", "public.dns_records"),
            ("em_elements_pkey", "public.em_elements"),
            ("em_exports_pkey", "public.em_exports"),
            ("em_imports_pkey", "public.em_imports"),
            ("em_manifests_pkey", "public.em_manifests"),
            ("em_resources_pkey", "public.em_resources"),
            ("em_services_pkey", "public.em_services"),
            ("gcl_sdk_audit_logs_pkey", "public.gcl_sdk_audit_logs"),
            ("gcl_sdk_events_pkey", "public.gcl_sdk_events"),
            ("iam_binding_permissions_pkey", "public.iam_binding_permissions"),
            ("iam_binding_roles_pkey", "public.iam_binding_roles"),
            ("iam_clients_pkey", "public.iam_clients"),
            ("iam_idp_authorization_info_pkey", "public.iam_idp_authorization_info"),
            ("iam_idp_pkey", "public.iam_idp"),
            ("iam_organization_members_pkey", "public.iam_organization_members"),
            ("iam_organizations_pkey", "public.iam_organizations"),
            ("iam_permissions_pkey", "public.iam_permissions"),
            ("iam_projects_pkey", "public.iam_projects"),
            ("iam_roles_pkey", "public.iam_roles"),
            ("iam_tokens_pkey", "public.iam_tokens"),
            ("iam_users_pkey", "public.iam_users"),
            ("machine_pools_pkey", "public.machine_pools"),
            ("machines_pkey", "public.machines"),
            ("n_builders_pkey", "public.n_builders"),
            ("n_machine_pool_reservations_pkey", "public.n_machine_pool_reservations"),
            ("net_lb_backendpools_pkey", "public.net_lb_backendpools"),
            ("net_lb_pkey", "public.net_lb"),
            ("net_lb_vhosts_pkey", "public.net_lb_vhosts"),
            ("net_lb_vhosts_routes_pkey", "public.net_lb_vhosts_routes"),
            ("node_volumes_pkey", "public.node_volumes"),
            ("nodes_pkey", "public.nodes"),
            ("secret_certificates_pkey", "public.secret_certificates"),
            ("secret_passwords_pkey", "public.secret_passwords"),
            ("secret_rsa_keys_pkey", "public.secret_rsa_keys"),
            ("secret_ssh_keys_pkey", "public.secret_ssh_keys"),
            ("security_rules_pkey", "public.security_rules"),
            ("storage_certs_pkey", "public.storage_certs"),
            ("storage_passwords_pkey", "public.storage_passwords"),
            ("vs_profiles_pkey", "public.vs_profiles"),
            ("vs_values_pkey", "public.vs_values"),
            ("vs_variables_pkey", "public.vs_variables"),
        ]
        for conname, table in pk_names:
            if _constraint_exists(session, conname):
                session.execute(
                    f"ALTER TABLE ONLY {table} DROP CONSTRAINT IF EXISTS {conname}",
                    None,
                )

    # ==================================================================
    # UNIQUE CONSTRAINTS
    # ==================================================================

    def _upgrade_unique_constraints(self, session):
        constraints = [
            ("dns_domains_id_key", "public.dns_domains", "id"),
            ("em_elements_unique_name", "public.em_elements", "name"),
            ("em_exports_unique_name", "public.em_exports", "element, name"),
            ("em_imports_unique_name", "public.em_imports", "element, name"),
            (
                "secret_ssh_keys_user_target_target_public_key_key",
                "public.secret_ssh_keys",
                '"user", target, target_public_key',
            ),
            (
                "unique_em_elements_name_version_idx",
                "public.em_elements",
                "name, version",
            ),
            (
                "uq_organization_user",
                "public.iam_organization_members",
                'organization, "user"',
            ),
            ("vs_profiles_name_key", "public.vs_profiles", "name"),
        ]
        for conname, table, columns in constraints:
            if not _constraint_exists(session, conname):
                session.execute(
                    f"ALTER TABLE ONLY {table} ADD CONSTRAINT {conname} UNIQUE ({columns})",
                    None,
                )

    def _downgrade_unique_constraints(self, session):
        uk_names = [
            ("dns_domains_id_key", "public.dns_domains"),
            ("em_elements_unique_name", "public.em_elements"),
            ("em_exports_unique_name", "public.em_exports"),
            ("em_imports_unique_name", "public.em_imports"),
            (
                "secret_ssh_keys_user_target_target_public_key_key",
                "public.secret_ssh_keys",
            ),
            ("unique_em_elements_name_version_idx", "public.em_elements"),
            ("uq_organization_user", "public.iam_organization_members"),
            ("vs_profiles_name_key", "public.vs_profiles"),
        ]
        for conname, table in uk_names:
            if _constraint_exists(session, conname):
                session.execute(
                    f"ALTER TABLE ONLY {table} DROP CONSTRAINT IF EXISTS {conname}",
                    None,
                )

    # ==================================================================
    # FOREIGN KEYS
    # ==================================================================

    def _upgrade_foreign_keys(self, session):
        fks = [
            (
                "compute_machine_volumes_machine_fkey",
                "public.compute_machine_volumes",
                "machine",
                "public.machines",
                "uuid",
                "SET NULL",
            ),
            (
                "compute_machine_volumes_node_volume_fkey",
                "public.compute_machine_volumes",
                "node_volume",
                "public.node_volumes",
                "uuid",
                "CASCADE",
            ),
            (
                "compute_machine_volumes_pool_fkey",
                "public.compute_machine_volumes",
                "pool",
                "public.machine_pools",
                "uuid",
                "SET NULL",
            ),
            (
                "compute_net_interfaces_machine_fkey",
                "public.compute_net_interfaces",
                "machine",
                "public.machines",
                "uuid",
                "CASCADE",
            ),
            (
                "compute_placement_policies_domain_fkey",
                "public.compute_placement_policies",
                "domain",
                "public.compute_placement_domains",
                "uuid",
                "RESTRICT",
            ),
            (
                "compute_placement_policies_zone_fkey",
                "public.compute_placement_policies",
                "zone",
                "public.compute_placement_zones",
                "uuid",
                "RESTRICT",
            ),
            (
                "compute_placement_policy_allocations_node_fkey",
                "public.compute_placement_policy_allocations",
                "node",
                "public.nodes",
                "uuid",
                "CASCADE",
            ),
            (
                "compute_placement_policy_allocations_policy_fkey",
                "public.compute_placement_policy_allocations",
                "policy",
                "public.compute_placement_policies",
                "uuid",
                "CASCADE",
            ),
            (
                "compute_placement_zones_domain_fkey",
                "public.compute_placement_zones",
                "domain",
                "public.compute_placement_domains",
                "uuid",
                "RESTRICT",
            ),
            (
                "compute_ports_machine_fkey",
                "public.compute_ports",
                "machine",
                "public.machines",
                "uuid",
                "CASCADE",
            ),
            (
                "compute_ports_node_fkey",
                "public.compute_ports",
                "node",
                "public.nodes",
                "uuid",
                "CASCADE",
            ),
            (
                "compute_ports_subnet_fkey",
                "public.compute_ports",
                "subnet",
                "public.compute_subnets",
                "uuid",
                "RESTRICT",
            ),
            (
                "compute_subnets_network_fkey",
                "public.compute_subnets",
                "network",
                "public.compute_networks",
                "uuid",
                "RESTRICT",
            ),
            (
                "dns_domainmetadata_domain_id_fkey",
                "public.dns_domainmetadata",
                "domain_id",
                "public.dns_domains",
                "id",
                "CASCADE",
            ),
            (
                "dns_records_domain_fkey",
                "public.dns_records",
                "domain",
                "public.dns_domains",
                "uuid",
                "CASCADE",
            ),
            (
                "dns_records_domain_id_fkey",
                "public.dns_records",
                "domain_id",
                "public.dns_domains",
                "id",
                "RESTRICT",
            ),
            (
                "em_elements_manifest_fkey",
                "public.em_elements",
                "manifest",
                "public.em_manifests",
                "uuid",
                "RESTRICT",
            ),
            (
                "em_elements_profile_fkey",
                "public.em_elements",
                "profile",
                "public.vs_profiles",
                "uuid",
                "RESTRICT",
            ),
            (
                "em_exports_element_fkey",
                "public.em_exports",
                "element",
                "public.em_elements",
                "uuid",
                "CASCADE",
            ),
            (
                "em_imports_element_fkey",
                "public.em_imports",
                "element",
                "public.em_elements",
                "uuid",
                "CASCADE",
            ),
            (
                "em_imports_from_element_fkey",
                "public.em_imports",
                "from_element",
                "public.em_elements",
                "uuid",
                "CASCADE",
            ),
            (
                "em_imports_from_resource_fkey",
                "public.em_imports",
                "from_resource",
                "public.em_resources",
                "uuid",
                "CASCADE",
            ),
            (
                "em_resources_actual_resource_fkey",
                "public.em_resources",
                "actual_resource",
                "public.ua_actual_resources",
                "res_uuid",
                "SET NULL",
            ),
            (
                "em_resources_element_fkey",
                "public.em_resources",
                "element",
                "public.em_elements",
                "uuid",
                "CASCADE",
            ),
            (
                "em_resources_target_resource_fkey",
                "public.em_resources",
                "target_resource",
                "public.ua_target_resources",
                "res_uuid",
                None,
            ),
            (
                "iam_binding_permissions_permission_fkey",
                "public.iam_binding_permissions",
                "permission",
                "public.iam_permissions",
                "uuid",
                None,
            ),
            (
                "iam_binding_permissions_role_fkey",
                "public.iam_binding_permissions",
                "role",
                "public.iam_roles",
                "uuid",
                None,
            ),
            (
                "iam_binding_roles_project_fkey",
                "public.iam_binding_roles",
                "project",
                "public.iam_projects",
                "uuid",
                None,
            ),
            (
                "iam_binding_roles_role_fkey",
                "public.iam_binding_roles",
                "role",
                "public.iam_roles",
                "uuid",
                None,
            ),
            (
                "iam_binding_roles_user_fkey",
                "public.iam_binding_roles",
                '"user"',
                "public.iam_users",
                "uuid",
                None,
            ),
            (
                "iam_idp_authorization_info_idp_fkey",
                "public.iam_idp_authorization_info",
                "idp",
                "public.iam_idp",
                "uuid",
                None,
            ),
            (
                "iam_idp_authorization_info_token_fkey",
                "public.iam_idp_authorization_info",
                "token",
                "public.iam_tokens",
                "uuid",
                None,
            ),
            (
                "iam_idp_iam_client_fkey",
                "public.iam_idp",
                "iam_client",
                "public.iam_clients",
                "uuid",
                "RESTRICT",
            ),
            (
                "iam_organization_members_organization_fkey",
                "public.iam_organization_members",
                "organization",
                "public.iam_organizations",
                "uuid",
                "CASCADE",
            ),
            (
                "iam_organization_members_user_fkey",
                "public.iam_organization_members",
                '"user"',
                "public.iam_users",
                "uuid",
                "CASCADE",
            ),
            (
                "iam_projects_organization_fkey",
                "public.iam_projects",
                "organization",
                "public.iam_organizations",
                "uuid",
                None,
            ),
            (
                "iam_tokens_iam_client_fkey",
                "public.iam_tokens",
                "iam_client",
                "public.iam_clients",
                "uuid",
                "CASCADE",
            ),
            (
                "iam_tokens_project_fkey",
                "public.iam_tokens",
                "project",
                "public.iam_projects",
                "uuid",
                "CASCADE",
            ),
            (
                "iam_tokens_user_fkey",
                "public.iam_tokens",
                '"user"',
                "public.iam_users",
                "uuid",
                "CASCADE",
            ),
            (
                "machines_node_fkey",
                "public.machines",
                "node",
                "public.nodes",
                "uuid",
                "CASCADE",
            ),
            (
                "machines_pool_fkey",
                "public.machines",
                "pool",
                "public.machine_pools",
                "uuid",
                "SET NULL",
            ),
            (
                "n_machine_pool_reservations_machine_fkey",
                "public.n_machine_pool_reservations",
                "machine",
                "public.machines",
                "uuid",
                "CASCADE",
            ),
            (
                "n_machine_pool_reservations_pool_fkey",
                "public.n_machine_pool_reservations",
                "pool",
                "public.machine_pools",
                "uuid",
                "CASCADE",
            ),
            (
                "net_lb_backendpools_parent_fkey",
                "public.net_lb_backendpools",
                "parent",
                "public.net_lb",
                "uuid",
                None,
            ),
            (
                "net_lb_vhosts_parent_fkey",
                "public.net_lb_vhosts",
                "parent",
                "public.net_lb",
                "uuid",
                None,
            ),
            (
                "net_lb_vhosts_routes_parent_fkey",
                "public.net_lb_vhosts_routes",
                "parent",
                "public.net_lb_vhosts",
                "uuid",
                None,
            ),
            (
                "node_volumes_node_fkey",
                "public.node_volumes",
                "node",
                "public.nodes",
                "uuid",
                "CASCADE",
            ),
            (
                "node_volumes_pool_fkey",
                "public.node_volumes",
                "pool",
                "public.machine_pools",
                "uuid",
                "SET NULL",
            ),
            (
                "nodes_node_set_fkey",
                "public.nodes",
                "node_set",
                "public.compute_sets",
                "uuid",
                "CASCADE",
            ),
            (
                "vs_values_variable_fkey",
                "public.vs_values",
                "variable",
                "public.vs_variables",
                "uuid",
                "SET NULL",
            ),
        ]
        for conname, src_table, src_col, ref_table, ref_col, on_delete in fks:
            if _constraint_exists(session, conname):
                continue
            clause = f"REFERENCES {ref_table}({ref_col})"
            if on_delete:
                clause += f" ON DELETE {on_delete}"
            session.execute(
                f"ALTER TABLE ONLY {src_table} ADD CONSTRAINT {conname} FOREIGN KEY ({src_col}) {clause}",
                None,
            )

    def _downgrade_foreign_keys(self, session):
        fk_names = [
            ("compute_machine_volumes_machine_fkey", "public.compute_machine_volumes"),
            (
                "compute_machine_volumes_node_volume_fkey",
                "public.compute_machine_volumes",
            ),
            ("compute_machine_volumes_pool_fkey", "public.compute_machine_volumes"),
            ("compute_net_interfaces_machine_fkey", "public.compute_net_interfaces"),
            (
                "compute_placement_policies_domain_fkey",
                "public.compute_placement_policies",
            ),
            (
                "compute_placement_policies_zone_fkey",
                "public.compute_placement_policies",
            ),
            (
                "compute_placement_policy_allocations_node_fkey",
                "public.compute_placement_policy_allocations",
            ),
            (
                "compute_placement_policy_allocations_policy_fkey",
                "public.compute_placement_policy_allocations",
            ),
            ("compute_placement_zones_domain_fkey", "public.compute_placement_zones"),
            ("compute_ports_machine_fkey", "public.compute_ports"),
            ("compute_ports_node_fkey", "public.compute_ports"),
            ("compute_ports_subnet_fkey", "public.compute_ports"),
            ("compute_subnets_network_fkey", "public.compute_subnets"),
            ("dns_domainmetadata_domain_id_fkey", "public.dns_domainmetadata"),
            ("dns_records_domain_fkey", "public.dns_records"),
            ("dns_records_domain_id_fkey", "public.dns_records"),
            ("em_elements_manifest_fkey", "public.em_elements"),
            ("em_elements_profile_fkey", "public.em_elements"),
            ("em_exports_element_fkey", "public.em_exports"),
            ("em_imports_element_fkey", "public.em_imports"),
            ("em_imports_from_element_fkey", "public.em_imports"),
            ("em_imports_from_resource_fkey", "public.em_imports"),
            ("em_resources_actual_resource_fkey", "public.em_resources"),
            ("em_resources_element_fkey", "public.em_resources"),
            ("em_resources_target_resource_fkey", "public.em_resources"),
            (
                "iam_binding_permissions_permission_fkey",
                "public.iam_binding_permissions",
            ),
            ("iam_binding_permissions_role_fkey", "public.iam_binding_permissions"),
            ("iam_binding_roles_project_fkey", "public.iam_binding_roles"),
            ("iam_binding_roles_role_fkey", "public.iam_binding_roles"),
            ("iam_binding_roles_user_fkey", "public.iam_binding_roles"),
            (
                "iam_idp_authorization_info_idp_fkey",
                "public.iam_idp_authorization_info",
            ),
            (
                "iam_idp_authorization_info_token_fkey",
                "public.iam_idp_authorization_info",
            ),
            ("iam_idp_iam_client_fkey", "public.iam_idp"),
            (
                "iam_organization_members_organization_fkey",
                "public.iam_organization_members",
            ),
            ("iam_organization_members_user_fkey", "public.iam_organization_members"),
            ("iam_projects_organization_fkey", "public.iam_projects"),
            ("iam_tokens_iam_client_fkey", "public.iam_tokens"),
            ("iam_tokens_project_fkey", "public.iam_tokens"),
            ("iam_tokens_user_fkey", "public.iam_tokens"),
            ("machines_node_fkey", "public.machines"),
            ("machines_pool_fkey", "public.machines"),
            (
                "n_machine_pool_reservations_machine_fkey",
                "public.n_machine_pool_reservations",
            ),
            (
                "n_machine_pool_reservations_pool_fkey",
                "public.n_machine_pool_reservations",
            ),
            ("net_lb_backendpools_parent_fkey", "public.net_lb_backendpools"),
            ("net_lb_vhosts_parent_fkey", "public.net_lb_vhosts"),
            ("net_lb_vhosts_routes_parent_fkey", "public.net_lb_vhosts_routes"),
            ("node_volumes_node_fkey", "public.node_volumes"),
            ("node_volumes_pool_fkey", "public.node_volumes"),
            ("nodes_node_set_fkey", "public.nodes"),
            ("vs_values_variable_fkey", "public.vs_values"),
        ]
        for conname, table in fk_names:
            if _constraint_exists(session, conname):
                session.execute(
                    f"ALTER TABLE ONLY {table} DROP CONSTRAINT IF EXISTS {conname}",
                    None,
                )

    # ==================================================================
    # INDEXES
    # ==================================================================

    def _upgrade_indexes(self, session):
        indexes = [
            "CREATE INDEX IF NOT EXISTS compute_net_interfaces_machine_id_idx ON public.compute_net_interfaces USING btree (machine)",
            "CREATE INDEX IF NOT EXISTS compute_networks_project_id_idx ON public.compute_networks USING btree (project_id)",
            "CREATE INDEX IF NOT EXISTS compute_placement_policies_domain_idx ON public.compute_placement_policies USING btree (domain)",
            "CREATE INDEX IF NOT EXISTS compute_placement_policies_project_id_idx ON public.compute_placement_policies USING btree (project_id)",
            "CREATE INDEX IF NOT EXISTS compute_placement_policies_zone_idx ON public.compute_placement_policies USING btree (zone)",
            "CREATE INDEX IF NOT EXISTS compute_placement_policy_allocations_node_idx ON public.compute_placement_policy_allocations USING btree (node)",
            "CREATE INDEX IF NOT EXISTS compute_placement_policy_allocations_policy_idx ON public.compute_placement_policy_allocations USING btree (policy)",
            "CREATE INDEX IF NOT EXISTS compute_placement_zones_domain_idx ON public.compute_placement_zones USING btree (domain)",
            "CREATE INDEX IF NOT EXISTS compute_ports_node_id_idx ON public.compute_ports USING btree (node)",
            "CREATE INDEX IF NOT EXISTS compute_ports_project_id_idx ON public.compute_ports USING btree (project_id)",
            "CREATE INDEX IF NOT EXISTS compute_ports_subnet_id_idx ON public.compute_ports USING btree (subnet)",
            "CREATE INDEX IF NOT EXISTS compute_sets_project_id_idx ON public.compute_sets USING btree (project_id)",
            "CREATE INDEX IF NOT EXISTS compute_subnets_network_id_idx ON public.compute_subnets USING btree (network)",
            "CREATE INDEX IF NOT EXISTS compute_subnets_project_id_idx ON public.compute_subnets USING btree (project_id)",
            "CREATE INDEX IF NOT EXISTS config_configs_project_id_idx ON public.config_configs USING btree (project_id)",
            "CREATE INDEX IF NOT EXISTS dns_domainmetadata_domain_id_idx ON public.dns_domainmetadata USING btree (domain_id)",
            "CREATE INDEX IF NOT EXISTS dns_domains_catalog_idx ON public.dns_domains USING btree (catalog)",
            "CREATE INDEX IF NOT EXISTS dns_domains_project_id_name_idx ON public.dns_domains USING btree (project_id, name)",
            "CREATE INDEX IF NOT EXISTS dns_records_project_id_idx ON public.dns_records USING btree (project_id)",
            "CREATE INDEX IF NOT EXISTS dns_records_updated_at_idx ON public.dns_records USING btree (updated_at)",
            "CREATE INDEX IF NOT EXISTS domain_id ON public.dns_records USING btree (domain_id)",
            "CREATE INDEX IF NOT EXISTS em_exports_element_idx ON public.em_exports USING btree (element)",
            "CREATE INDEX IF NOT EXISTS em_imports_element_idx ON public.em_imports USING btree (element)",
            "CREATE INDEX IF NOT EXISTS em_resources_element_idx ON public.em_resources USING btree (element)",
            "CREATE INDEX IF NOT EXISTS em_services_project_id_idx ON public.em_services USING btree (project_id)",
            "CREATE INDEX IF NOT EXISTS gcl_sdk_audit_logs_object_type_action_idx ON public.gcl_sdk_audit_logs USING btree (object_type, action)",
            "CREATE INDEX IF NOT EXISTS iam_binding_permissions_role_permission_idx ON public.iam_binding_permissions USING btree (role, permission)",
            "CREATE INDEX IF NOT EXISTS iam_binding_roles_project_idx ON public.iam_binding_roles USING btree (project)",
            "CREATE INDEX IF NOT EXISTS iam_binding_roles_role_idx ON public.iam_binding_roles USING btree (role)",
            'CREATE INDEX IF NOT EXISTS iam_binding_roles_user_idx ON public.iam_binding_roles USING btree ("user")',
            "CREATE INDEX IF NOT EXISTS iam_organizations_name_idx ON public.iam_organizations USING btree (name)",
            "CREATE INDEX IF NOT EXISTS iam_projects_name_idx ON public.iam_projects USING btree (name)",
            "CREATE INDEX IF NOT EXISTS iam_projects_organization_idx ON public.iam_projects USING btree (organization)",
            "CREATE INDEX IF NOT EXISTS iam_roles_name_idx ON public.iam_roles USING btree (name)",
            "CREATE INDEX IF NOT EXISTS iam_tokens_iam_client_idx ON public.iam_tokens USING btree (iam_client)",
            "CREATE INDEX IF NOT EXISTS idx_compute_machine_volumes_machine ON public.compute_machine_volumes USING btree (machine)",
            "CREATE INDEX IF NOT EXISTS idx_compute_machine_volumes_node_volume ON public.compute_machine_volumes USING btree (node_volume)",
            "CREATE INDEX IF NOT EXISTS idx_compute_machine_volumes_pool ON public.compute_machine_volumes USING btree (pool)",
            "CREATE INDEX IF NOT EXISTS idx_compute_machine_volumes_project_id ON public.compute_machine_volumes USING btree (project_id)",
            "CREATE INDEX IF NOT EXISTS nametype_index ON public.dns_records USING btree (name, type)",
            "CREATE INDEX IF NOT EXISTS net_lb_backendpools_parent_name_idx ON public.net_lb_backendpools USING btree (parent, name)",
            "CREATE INDEX IF NOT EXISTS net_lb_backendpools_project_id_idx ON public.net_lb_backendpools USING btree (project_id)",
            "CREATE INDEX IF NOT EXISTS net_lb_project_id_name_idx ON public.net_lb USING btree (project_id, name)",
            "CREATE INDEX IF NOT EXISTS net_lb_vhosts_parent_name_idx ON public.net_lb_vhosts USING btree (parent, name)",
            "CREATE INDEX IF NOT EXISTS net_lb_vhosts_parent_port_domains_idx ON public.net_lb_vhosts USING btree (parent, port, domains)",
            "CREATE INDEX IF NOT EXISTS net_lb_vhosts_project_id_idx ON public.net_lb_vhosts USING btree (project_id)",
            "CREATE INDEX IF NOT EXISTS net_lb_vhosts_routes_parent_name_idx ON public.net_lb_vhosts_routes USING btree (parent, name)",
            "CREATE INDEX IF NOT EXISTS net_lb_vhosts_routes_project_id_idx ON public.net_lb_vhosts_routes USING btree (project_id)",
            "CREATE INDEX IF NOT EXISTS rec_name_index ON public.dns_records USING btree (name)",
            "CREATE INDEX IF NOT EXISTS recordorder ON public.dns_records USING btree (domain_id, ordername text_pattern_ops)",
            "CREATE INDEX IF NOT EXISTS secret_certificates_project_id_idx ON public.secret_certificates USING btree (project_id)",
            "CREATE INDEX IF NOT EXISTS secret_passwords_project_id_idx ON public.secret_passwords USING btree (project_id)",
            "CREATE INDEX IF NOT EXISTS secret_rsa_keys_project_id_idx ON public.secret_rsa_keys USING btree (project_id)",
            "CREATE INDEX IF NOT EXISTS secret_ssh_keys_project_id_idx ON public.secret_ssh_keys USING btree (project_id)",
            "CREATE INDEX IF NOT EXISTS security_rules_name_idx ON public.security_rules USING btree (name)",
            "CREATE INDEX IF NOT EXISTS security_rules_project_id_idx ON public.security_rules USING btree (project_id)",
            "CREATE INDEX IF NOT EXISTS vs_profiles_profile_type_idx ON public.vs_profiles USING btree (profile_type)",
            "CREATE INDEX IF NOT EXISTS vs_profiles_project_id_idx ON public.vs_profiles USING btree (project_id)",
            "CREATE INDEX IF NOT EXISTS vs_values_project_id_idx ON public.vs_values USING btree (project_id)",
            "CREATE INDEX IF NOT EXISTS vs_variables_project_id_idx ON public.vs_variables USING btree (project_id)",
        ]
        for stmt in indexes:
            session.execute(stmt, None)

    def _downgrade_indexes(self, session):
        index_names = [
            "compute_net_interfaces_machine_id_idx",
            "compute_networks_project_id_idx",
            "compute_placement_policies_domain_idx",
            "compute_placement_policies_project_id_idx",
            "compute_placement_policies_zone_idx",
            "compute_placement_policy_allocations_node_idx",
            "compute_placement_policy_allocations_policy_idx",
            "compute_placement_zones_domain_idx",
            "compute_ports_node_id_idx",
            "compute_ports_project_id_idx",
            "compute_ports_subnet_id_idx",
            "compute_sets_project_id_idx",
            "compute_subnets_network_id_idx",
            "compute_subnets_project_id_idx",
            "config_configs_project_id_idx",
            "dns_domainmetadata_domain_id_idx",
            "dns_domains_catalog_idx",
            "dns_domains_project_id_name_idx",
            "dns_records_project_id_idx",
            "dns_records_updated_at_idx",
            "domain_id",
            "em_exports_element_idx",
            "em_imports_element_idx",
            "em_resources_element_idx",
            "em_services_project_id_idx",
            "gcl_sdk_audit_logs_object_type_action_idx",
            "iam_binding_permissions_role_permission_idx",
            "iam_binding_roles_project_idx",
            "iam_binding_roles_role_idx",
            "iam_binding_roles_user_idx",
            "iam_organizations_name_idx",
            "iam_projects_name_idx",
            "iam_projects_organization_idx",
            "iam_roles_name_idx",
            "iam_tokens_iam_client_idx",
            "idx_compute_machine_volumes_machine",
            "idx_compute_machine_volumes_node_volume",
            "idx_compute_machine_volumes_pool",
            "idx_compute_machine_volumes_project_id",
            "nametype_index",
            "net_lb_backendpools_parent_name_idx",
            "net_lb_backendpools_project_id_idx",
            "net_lb_project_id_name_idx",
            "net_lb_vhosts_parent_name_idx",
            "net_lb_vhosts_parent_port_domains_idx",
            "net_lb_vhosts_project_id_idx",
            "net_lb_vhosts_routes_parent_name_idx",
            "net_lb_vhosts_routes_project_id_idx",
            "rec_name_index",
            "recordorder",
            "secret_certificates_project_id_idx",
            "secret_passwords_project_id_idx",
            "secret_rsa_keys_project_id_idx",
            "secret_ssh_keys_project_id_idx",
            "security_rules_name_idx",
            "security_rules_project_id_idx",
            "vs_profiles_profile_type_idx",
            "vs_profiles_project_id_idx",
            "vs_values_project_id_idx",
            "vs_variables_project_id_idx",
        ]
        for idx in index_names:
            session.execute(f"DROP INDEX IF EXISTS public.{idx}", None)

    # ==================================================================
    # UNIQUE INDEXES
    # ==================================================================

    def _upgrade_unique_indexes(self, session):
        indexes = [
            "CREATE UNIQUE INDEX IF NOT EXISTS compute_net_interfaces_mac_machine_id_idx ON public.compute_net_interfaces USING btree (mac, machine)",
            "CREATE UNIQUE INDEX IF NOT EXISTS compute_ports_mac_subnet_id_idx ON public.compute_ports USING btree (mac, subnet)",
            "CREATE UNIQUE INDEX IF NOT EXISTS compute_ports_target_ipv4_subnet_id_idx ON public.compute_ports USING btree (target_ipv4, subnet)",
            "CREATE UNIQUE INDEX IF NOT EXISTS config_configs_path_target_id_idx ON public.config_configs USING btree (path, target)",
            "CREATE UNIQUE INDEX IF NOT EXISTS dns_domains_id_idx ON public.dns_domains USING btree (id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS dns_domains_name_idx ON public.dns_domains USING btree (name)",
            "CREATE UNIQUE INDEX IF NOT EXISTS em_services_path_target_id_idx ON public.em_services USING btree (path, target)",
            "CREATE UNIQUE INDEX IF NOT EXISTS iam_client_id_idx ON public.iam_clients USING btree (client_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS iam_permissions_name_idx ON public.iam_permissions USING btree (name)",
            "CREATE UNIQUE INDEX IF NOT EXISTS iam_users_email_lower_idx ON public.iam_users USING btree (lower((email)::text))",
            "CREATE UNIQUE INDEX IF NOT EXISTS iam_users_name_lower_idx ON public.iam_users USING btree (lower((name)::text))",
            "CREATE UNIQUE INDEX IF NOT EXISTS machine_pools_driver_spec_connection_uri_idx ON public.machine_pools USING btree (((driver_spec ->> 'connection_uri'::text)))",
            "CREATE UNIQUE INDEX IF NOT EXISTS vs_values_one_manual_selected_per_variable ON public.vs_values USING btree (variable) WHERE (manual_selected = true)",
        ]
        for stmt in indexes:
            session.execute(stmt, None)

    def _downgrade_unique_indexes(self, session):
        index_names = [
            "compute_net_interfaces_mac_machine_id_idx",
            "compute_ports_mac_subnet_id_idx",
            "compute_ports_target_ipv4_subnet_id_idx",
            "config_configs_path_target_id_idx",
            "dns_domains_id_idx",
            "dns_domains_name_idx",
            "em_services_path_target_id_idx",
            # "iam_client_id_idx",
            "iam_permissions_name_idx",
            "iam_users_email_lower_idx",
            "iam_users_name_lower_idx",
            "machine_pools_driver_spec_connection_uri_idx",
            "vs_values_one_manual_selected_per_variable",
        ]
        for idx in index_names:
            session.execute(f"DROP INDEX IF EXISTS public.{idx}", None)

    # ==================================================================
    # DATA (bootstrap inserts)
    # ==================================================================

    def _upgrade_data(self, session):
        self._upgrade_bootstrap_admin_data(session)
        self._upgrade_default_org_and_roles(session)
        self._upgrade_iam_core(session)
        self._upgrade_compute(session)
        self._upgrade_iam_resource_permissions(session)
        self._upgrade_dns(session)
        self._upgrade_compute_node_set(session)
        self._upgrade_org_members(session)
        self._upgrade_secret_password(session)
        self._upgrade_actual_resources(session)
        self._upgrade_network_lb(session)
        self._upgrade_service_token(session)
        self._upgrade_iam_user_create(session)

    def _downgrade_data(self, session):
        self._downgrade_iam_user_create(session)
        self._downgrade_service_token(session)
        self._downgrade_network_lb(session)
        self._downgrade_actual_resources(session)
        self._downgrade_secret_password(session)
        self._downgrade_org_members(session)
        self._downgrade_compute_node_set(session)
        self._downgrade_dns(session)
        self._downgrade_iam_resource_permissions(session)
        self._downgrade_compute(session)
        self._downgrade_iam_core(session)
        self._downgrade_default_org_and_roles(session)
        self._downgrade_bootstrap_admin_data(session)

    # ------------------------------------------------------------------
    # Bootstrap admin data
    # ------------------------------------------------------------------

    def _upgrade_bootstrap_admin_data(self, session):
        default_admin_salt = "d4JJ9QYuEEJxHCFja9FZskG4"
        default_client_secret = os.getenv("DEFAULT_CLIENT_SECRET", "GenesisCoreSecret")
        global_salt = os.getenv("GLOBAL_SALT", "FOy/2kwwdn0ig1QOq7cestqe")
        default_admin_secret = _generate_hash(
            secret=os.getenv("ADMIN_PASSWORD", "admin"),
            secret_salt=default_admin_salt,
            global_salt=global_salt,
        )
        default_client_secret_hash = _generate_hash(
            secret=default_client_secret,
            secret_salt=default_admin_salt,
            global_salt=global_salt,
        )
        statements = [
            # Admin user
            f"""INSERT INTO "iam_users" (
                "uuid", "name", "description", "first_name", "last_name",
                "email", "secret_hash", "salt"
            ) VALUES (
                '00000000-0000-0000-0000-000000000000',
                'admin',
                'System administrator',
                'Admin',
                'User',
                'admin@example.com',
                '{default_admin_secret}',
                '{default_admin_salt}'
            ) ON CONFLICT (uuid) DO NOTHING;""",
            # Admin organization
            """INSERT INTO "iam_organizations" (
                "uuid", "name", "description"
            ) VALUES (
                '00000000-0000-0000-0000-000000000000',
                'admin', 'Admin Organization'
            ) ON CONFLICT (uuid) DO NOTHING;""",
            # Admin project
            """INSERT INTO "iam_projects" (
                "uuid", "name", description, organization
            ) VALUES (
                '00000000-0000-0000-0000-000000000000',
                'admin', 'Admin Project',
                '00000000-0000-0000-0000-000000000000'
            ) ON CONFLICT (uuid) DO NOTHING;""",
            # Admin role
            """INSERT INTO "iam_roles" (
                "uuid", "name", "description"
            ) VALUES (
                '00000000-0000-0000-0000-000000000000',
                'admin', 'Admin Role'
            ) ON CONFLICT (uuid) DO NOTHING;""",
            # Wildcard permission
            """INSERT INTO "iam_permissions" (
                "uuid", "name", "description"
            ) VALUES (
                '00000000-0000-0000-0000-000000000000',
                '*.*.*', 'Allow All'
            ) ON CONFLICT (uuid) DO NOTHING;""",
            # Admin binding permission
            """INSERT INTO "iam_binding_permissions" (
                "uuid", "role", "permission"
            ) VALUES (
                '00000000-0000-0000-0000-000000000000',
                '00000000-0000-0000-0000-000000000000',
                '00000000-0000-0000-0000-000000000000'
            ) ON CONFLICT (uuid) DO NOTHING;""",
            # Admin role binding
            """INSERT INTO "iam_binding_roles" (
                "uuid", "user", "role", "description"
            ) VALUES (
                '00000000-0000-0000-0000-000000000000',
                '00000000-0000-0000-0000-000000000000',
                '00000000-0000-0000-0000-000000000000',
                'Super Administrator'
            ) ON CONFLICT (uuid) DO NOTHING;""",
            # Default IAM client
            f"""INSERT INTO "iam_clients" (
                "uuid", "name", "description", "client_id",
                "secret_hash", "salt"
            ) VALUES(
                '{DEFAULT_IAM_CLIENT_UUID}',
                'GenesisCoreClient',
                'Exordos Core OIDC Client',
                'GenesisCoreClientId',
                '{default_client_secret_hash}',
                '{default_admin_salt}'
            ) ON CONFLICT (uuid) DO NOTHING;""",
        ]
        for stmt in statements:
            session.execute(stmt)

    def _downgrade_bootstrap_admin_data(self, session):
        statements = [
            # Delete all binding permissions for bindings referencing admin
            "DELETE FROM iam_binding_permissions WHERE role IN (SELECT uuid FROM iam_binding_roles WHERE \"user\" = '00000000-0000-0000-0000-000000000000');",
            # Delete ALL role bindings referencing admin user (catches test-created ones)
            "DELETE FROM iam_binding_roles WHERE \"user\" = '00000000-0000-0000-0000-000000000000';",
            # Bootstrap data cleanup
            # f"DELETE FROM iam_clients WHERE uuid = '{DEFAULT_IAM_CLIENT_UUID}';",
            "DELETE FROM iam_binding_permissions WHERE uuid = '00000000-0000-0000-0000-000000000000';",
            "DELETE FROM iam_permissions WHERE uuid = '00000000-0000-0000-0000-000000000000';",
            "DELETE FROM iam_roles WHERE uuid = '00000000-0000-0000-0000-000000000000';",
            "DELETE FROM iam_projects WHERE uuid = '00000000-0000-0000-0000-000000000000';",
            "DELETE FROM iam_organizations WHERE uuid = '00000000-0000-0000-0000-000000000000';",
            "DELETE FROM iam_users WHERE uuid = '00000000-0000-0000-0000-000000000000';",
        ]
        for stmt in statements:
            session.execute(stmt)

    # ------------------------------------------------------------------
    # Default organisation and roles (Genesis Corporation, newcomer, owner)
    # ------------------------------------------------------------------

    def _upgrade_default_org_and_roles(self, session):
        statements = [
            f"""INSERT INTO "iam_organizations" (
                "uuid", "name", "description"
            ) VALUES (
                '{EXORDOS_CORE_ORGANIZATION_ID}',
                '{EXORDOS_CORE_ORGANIZATION_NAME}',
                '{EXORDOS_CORE_ORGANIZATION_DESCRIPTION}'
            ) ON CONFLICT (uuid) DO NOTHING;""",
            f"""INSERT INTO "iam_roles" (
                "uuid", "name", "description", "project_id"
            ) VALUES (
                '{NEWCOMER_ROLE_UUID}',
                '{NEWCOMER_ROLE_NAME}',
                '{NEWCOMER_ROLE_DESCRIPTION}',
                NULL
            ) ON CONFLICT (uuid) DO NOTHING;""",
            f"""INSERT INTO "iam_roles" (
                "uuid", "name", "description", "project_id"
            ) VALUES (
                '{OWNER_ROLE_UUID}',
                '{OWNER_ROLE_NAME}',
                '{OWNER_ROLE_DESCRIPTION}',
                NULL
            ) ON CONFLICT (uuid) DO NOTHING;""",
        ]
        for stmt in statements:
            session.execute(stmt)

    def _downgrade_default_org_and_roles(self, session):
        statements = [
            f"DELETE FROM iam_binding_permissions WHERE role = '{NEWCOMER_ROLE_UUID}';",
            f"DELETE FROM iam_binding_permissions WHERE role = '{OWNER_ROLE_UUID}';",
            f"DELETE FROM iam_binding_roles WHERE role = '{NEWCOMER_ROLE_UUID}';",
            f"DELETE FROM iam_binding_roles WHERE role = '{OWNER_ROLE_UUID}';",
            f"DELETE FROM iam_roles WHERE uuid = '{NEWCOMER_ROLE_UUID}';",
            f"DELETE FROM iam_roles WHERE uuid = '{OWNER_ROLE_UUID}';",
            f"DELETE FROM iam_organizations WHERE uuid = '{EXORDOS_CORE_ORGANIZATION_ID}';",
        ]
        for stmt in statements:
            session.execute(stmt)

    # ------------------------------------------------------------------
    # Core IAM permissions, project, and newcomer bindings (m0008)
    # ------------------------------------------------------------------

    def _upgrade_iam_core(self, session):
        iam_permissions = [
            (USER_LIST, "Allows listing users in the system"),
            (USER_READ_ALL, "Allows reading all user profiles"),
            (USER_WRITE_ALL, "Allows modifying any user`s data"),
            (USER_DELETE_ALL, "Allows deleting any user account"),
            (USER_DELETE, "Allows users to delete their own account"),
            (ORG_CREATE, "Allows creating new organizations"),
            (ORG_READ_ALL, "Allows viewing all organization details"),
            (ORG_WRITE_ALL, "Allows modifying any organization`s data"),
            (ORG_DELETE, "Allows deleting own organization"),
            (ORG_DELETE_ALL, "Allows deleting any organization"),
        ]
        for name, description in iam_permissions:
            session.execute(f"""
                INSERT INTO iam_permissions (
                    uuid, name, description
                ) VALUES (
                    '{PERMISSION_UUIDS[name]}',
                    '{name}',
                    '{description}'
                ) ON CONFLICT (uuid) DO NOTHING;
            """)
        session.execute(f"""
            INSERT INTO iam_projects (
                uuid, name, description, organization
            ) VALUES (
                '{IAM_PROJECT_UUID}',
                'iam-core',
                'Identity and Access Management Core Project',
                '{EXORDOS_CORE_ORGANIZATION_ID}'
            ) ON CONFLICT (uuid) DO NOTHING;
        """)
        for perm_name in [USER_DELETE, ORG_CREATE, ORG_DELETE]:
            session.execute(f"""
                INSERT INTO iam_binding_permissions (
                    uuid, role, permission, project_id
                ) VALUES (
                    gen_random_uuid(),
                    '{NEWCOMER_ROLE_UUID}',
                    '{PERMISSION_UUIDS[perm_name]}',
                    '{IAM_PROJECT_UUID}'
                );
            """)

    def _downgrade_iam_core(self, session):
        for permission_uuid in PERMISSION_UUIDS.values():
            session.execute(f"""
                DELETE FROM iam_binding_permissions
                WHERE permission = '{permission_uuid}';
            """)
        session.execute(f"""
            DELETE FROM iam_projects
            WHERE uuid = '{IAM_PROJECT_UUID}';
        """)
        for permission_uuid in PERMISSION_UUIDS.values():
            session.execute(f"""
                DELETE FROM iam_permissions
                WHERE uuid = '{permission_uuid}';
            """)

    # ------------------------------------------------------------------
    # Compute permissions, project, and owner bindings (m0012)
    # ------------------------------------------------------------------

    def _upgrade_compute(self, session):
        for name, description in COMPUTE_NODE_DEF_PERMISSIONS:
            session.execute(f"""
                INSERT INTO iam_permissions (
                    uuid, name, description
                ) VALUES (
                    '{_u(name)}',
                    '{name}',
                    '{description}'
                ) ON CONFLICT (uuid) DO NOTHING;
            """)
        session.execute(f"""
            INSERT INTO iam_projects (
                uuid, name, description, organization
            ) VALUES (
                '{COMPUTE_PROJECT_UUID}',
                'compute-core',
                'Compute and Baremetal Core Project',
                '{EXORDOS_CORE_ORGANIZATION_ID}'
            ) ON CONFLICT (uuid) DO NOTHING;
        """)
        for name, _ in COMPUTE_NODE_DEF_PERMISSIONS:
            session.execute(f"""
                INSERT INTO iam_binding_permissions (
                    uuid, role, permission, project_id
                ) VALUES (
                    gen_random_uuid(),
                    '{OWNER_ROLE_UUID}',
                    '{_u(name)}',
                    '{COMPUTE_PROJECT_UUID}'
                );
            """)

    def _downgrade_compute(self, session):
        for name, _ in COMPUTE_NODE_DEF_PERMISSIONS:
            session.execute(f"""
                DELETE FROM iam_binding_permissions
                WHERE permission = '{_u(name)}';
            """)
        session.execute(f"""
            DELETE FROM iam_projects
            WHERE uuid = '{COMPUTE_PROJECT_UUID}';
        """)
        for name, _ in COMPUTE_NODE_DEF_PERMISSIONS:
            session.execute(f"""
                DELETE FROM iam_permissions
                WHERE uuid = '{_u(name)}';
            """)

    # ------------------------------------------------------------------
    # Full IAM resource permissions (m0016)
    # ------------------------------------------------------------------

    def _upgrade_iam_resource_permissions(self, session):
        iam_resource_permissions = [
            (PERMISSION_PROJECT_LIST_ALL, "Allows listing projects in the system"),
            (PERMISSION_PROJECT_READ_ALL, "Allows reading all project details"),
            (PERMISSION_PROJECT_WRITE_ALL, "Allows modifying any project`s data"),
            (PERMISSION_PROJECT_DELETE_ALL, "Allows deleting any project"),
            (PERMISSION_PERMISSION_CREATE, "Allows creating new permissions"),
            (PERMISSION_PERMISSION_READ, "Allows reading permissions"),
            (PERMISSION_PERMISSION_UPDATE, "Allows updating existing permissions"),
            (PERMISSION_PERMISSION_DELETE, "Allows deleting permissions"),
            (
                PERMISSION_PERMISSION_BINDING_CREATE,
                "Allows creating permission bindings",
            ),
            (PERMISSION_PERMISSION_BINDING_READ, "Allows reading permission bindings"),
            (
                PERMISSION_PERMISSION_BINDING_UPDATE,
                "Allows updating permission bindings",
            ),
            (
                PERMISSION_PERMISSION_BINDING_DELETE,
                "Allows deleting permission bindings",
            ),
            (PERMISSION_ROLE_CREATE, "Allows creating new roles"),
            (PERMISSION_ROLE_READ, "Allows reading roles"),
            (PERMISSION_ROLE_UPDATE, "Allows updating existing roles"),
            (PERMISSION_ROLE_DELETE, "Allows deleting roles"),
            (PERMISSION_ROLE_BINDING_CREATE, "Allows creating role bindings"),
            (PERMISSION_ROLE_BINDING_READ, "Allows reading role bindings"),
            (PERMISSION_ROLE_BINDING_UPDATE, "Allows updating role bindings"),
            (PERMISSION_ROLE_BINDING_DELETE, "Allows deleting role bindings"),
            (PERMISSION_IAM_CLIENT_CREATE, "Allows creating IAM clients"),
            (PERMISSION_IAM_CLIENT_READ_ALL, "Allows reading all IAM clients"),
            (PERMISSION_IAM_CLIENT_UPDATE, "Allows updating IAM clients"),
            (PERMISSION_IAM_CLIENT_DELETE, "Allows deleting IAM clients"),
        ]
        for name, description in iam_resource_permissions:
            session.execute(f"""
                INSERT INTO iam_permissions (
                    uuid, name, description
                ) VALUES (
                    '{PERMISSION_UUIDS[name]}',
                    '{name}',
                    '{description}'
                ) ON CONFLICT (uuid) DO NOTHING;
            """)

    def _downgrade_iam_resource_permissions(self, session):
        iam_resource_permission_names = [
            PERMISSION_PROJECT_LIST_ALL,
            PERMISSION_PROJECT_READ_ALL,
            PERMISSION_PROJECT_WRITE_ALL,
            PERMISSION_PROJECT_DELETE_ALL,
            PERMISSION_PERMISSION_CREATE,
            PERMISSION_PERMISSION_READ,
            PERMISSION_PERMISSION_UPDATE,
            PERMISSION_PERMISSION_DELETE,
            PERMISSION_PERMISSION_BINDING_CREATE,
            PERMISSION_PERMISSION_BINDING_READ,
            PERMISSION_PERMISSION_BINDING_UPDATE,
            PERMISSION_PERMISSION_BINDING_DELETE,
            PERMISSION_ROLE_CREATE,
            PERMISSION_ROLE_READ,
            PERMISSION_ROLE_UPDATE,
            PERMISSION_ROLE_DELETE,
            PERMISSION_ROLE_BINDING_CREATE,
            PERMISSION_ROLE_BINDING_READ,
            PERMISSION_ROLE_BINDING_UPDATE,
            PERMISSION_ROLE_BINDING_DELETE,
            PERMISSION_IAM_CLIENT_CREATE,
            PERMISSION_IAM_CLIENT_READ_ALL,
            PERMISSION_IAM_CLIENT_UPDATE,
            PERMISSION_IAM_CLIENT_DELETE,
        ]
        for name in iam_resource_permission_names:
            session.execute(f"""
                DELETE FROM iam_binding_permissions
                WHERE permission = '{PERMISSION_UUIDS[name]}';
            """)
        for name in iam_resource_permission_names:
            session.execute(f"""
                DELETE FROM iam_permissions
                WHERE uuid = '{PERMISSION_UUIDS[name]}';
            """)

    # ------------------------------------------------------------------
    # DNS permissions, project, and owner bindings (m0021)
    # ------------------------------------------------------------------

    def _upgrade_dns(self, session):
        for name, description in DNS_NODE_DEF_PERMISSIONS:
            session.execute(f"""
                INSERT INTO iam_permissions (
                    uuid, name, description
                ) VALUES (
                    '{_u(name)}',
                    '{name}',
                    '{description}'
                ) ON CONFLICT (uuid) DO NOTHING;
            """)
        session.execute(f"""
            INSERT INTO iam_projects (
                uuid, name, description, organization
            ) VALUES (
                '{DNS_PROJECT_UUID}',
                'dns-core',
                'Dns Core Project',
                '{EXORDOS_CORE_ORGANIZATION_ID}'
            ) ON CONFLICT (uuid) DO NOTHING;
        """)
        for name, _ in DNS_NODE_DEF_PERMISSIONS:
            session.execute(f"""
                INSERT INTO iam_binding_permissions (
                    uuid, role, permission, project_id
                ) VALUES (
                    gen_random_uuid(),
                    '{OWNER_ROLE_UUID}',
                    '{_u(name)}',
                    '{DNS_PROJECT_UUID}'
                );
            """)

    def _downgrade_dns(self, session):
        for name, _ in DNS_NODE_DEF_PERMISSIONS:
            session.execute(f"""
                DELETE FROM iam_binding_permissions
                WHERE permission = '{_u(name)}';
            """)
        session.execute(f"""
            DELETE FROM iam_projects
            WHERE uuid = '{DNS_PROJECT_UUID}';
        """)
        for name, _ in DNS_NODE_DEF_PERMISSIONS:
            session.execute(f"""
                DELETE FROM iam_permissions
                WHERE uuid = '{_u(name)}';
            """)

    # ------------------------------------------------------------------
    # Compute node set permissions and bindings (m0031)
    # ------------------------------------------------------------------

    def _upgrade_compute_node_set(self, session):
        for name, description in COMPUTE_NODE_SET_DEF_PERMISSIONS:
            session.execute(f"""
                INSERT INTO iam_permissions (
                    uuid, name, description
                ) VALUES (
                    '{_u(name)}',
                    '{name}',
                    '{description}'
                ) ON CONFLICT (uuid) DO NOTHING;
            """)
        for name, _ in COMPUTE_NODE_SET_DEF_PERMISSIONS:
            session.execute(f"""
                INSERT INTO iam_binding_permissions (
                    uuid, role, permission, project_id
                ) VALUES (
                    '{_u("binding." + name)}',
                    '{OWNER_ROLE_UUID}',
                    '{_u(name)}',
                    '{COMPUTE_PROJECT_UUID}'
                ) ON CONFLICT (uuid) DO NOTHING;
            """)

    def _downgrade_compute_node_set(self, session):
        for name, _ in COMPUTE_NODE_SET_DEF_PERMISSIONS:
            session.execute(f"""
                DELETE FROM iam_binding_permissions
                WHERE permission = '{_u(name)}';
            """)
        for name, _ in COMPUTE_NODE_SET_DEF_PERMISSIONS:
            session.execute(f"""
                DELETE FROM iam_permissions
                WHERE uuid = '{_u(name)}';
            """)

    # ------------------------------------------------------------------
    # Organization member for admin user (m0003)
    # ------------------------------------------------------------------

    def _upgrade_org_members(self, session):
        session.execute("""
            INSERT INTO "iam_organization_members" (
                "uuid",
                "organization",
                "user",
                "role",
                "created_at",
                "updated_at"
            )
            SELECT
                gen_random_uuid(),
                o."uuid",
                '00000000-0000-0000-0000-000000000000',
                'OWNER',
                o."created_at",
                o."updated_at"
            FROM "iam_organizations" o
            ON CONFLICT ("organization", "user") DO NOTHING;
        """)

    def _downgrade_org_members(self, session):
        session.execute("""
            DELETE FROM "iam_organization_members"
            WHERE "user" = '00000000-0000-0000-0000-000000000000';
        """)

    # ------------------------------------------------------------------
    # Default HS256 secret password (m0039)
    # ------------------------------------------------------------------

    def _upgrade_secret_password(self, session):
        session.execute("""
            INSERT INTO "secret_passwords" (
                "uuid",
                "name",
                "description",
                "project_id",
                "constructor",
                "method",
                "value",
                "status"
            ) VALUES (
                '00000000-0000-0000-0000-000000000001',
                'iam-client-hs256-secret',
                'Default HS256 secret for IAM clients',
                '00000000-0000-0000-0000-000000000000',
                '{"kind": "plain"}'::jsonb,
                'MANUAL',
                'secret',
                'ACTIVE'
            ) ON CONFLICT ("uuid") DO NOTHING;
        """)
        session.execute(
            """
                UPDATE "iam_tokens"
                SET "iam_client" = %s
                WHERE "iam_client" IS NULL;
            """,
            (DEFAULT_IAM_CLIENT_UUID,),
        )

    def _downgrade_secret_password(self, session):
        session.execute("""
            DELETE FROM "secret_passwords"
            WHERE "uuid" = '00000000-0000-0000-0000-000000000001';
        """)

    # ------------------------------------------------------------------
    # Network LB permissions and bindings (m0053)
    # ------------------------------------------------------------------

    def _upgrade_network_lb(self, session):
        for name, description in NETWORK_LB_PERMISSIONS:
            session.execute(f"""
                INSERT INTO iam_permissions (
                    uuid, name, description
                ) VALUES (
                    '{_u(name)}',
                    '{name}',
                    '{description}'
                ) ON CONFLICT (uuid) DO NOTHING;
            """)
        for name, _ in NETWORK_LB_PERMISSIONS:
            session.execute(f"""
                INSERT INTO iam_binding_permissions (
                    uuid, role, permission, project_id
                ) VALUES (
                    '{_u("binding." + name)}',
                    '{OWNER_ROLE_UUID}',
                    '{_u(name)}',
                    '{COMPUTE_PROJECT_UUID}'
                ) ON CONFLICT (uuid) DO NOTHING;
            """)

    def _downgrade_network_lb(self, session):
        for name, _ in NETWORK_LB_PERMISSIONS:
            session.execute(f"""
                DELETE FROM iam_binding_permissions
                WHERE permission = '{_u(name)}';
            """)
        for name, _ in NETWORK_LB_PERMISSIONS:
            session.execute(f"""
                DELETE FROM iam_permissions
                WHERE uuid = '{_u(name)}';
            """)

    # ------------------------------------------------------------------
    # Service token permission and binding (m0055)
    # ------------------------------------------------------------------

    def _upgrade_service_token(self, session):
        for name, description in SERVICE_TOKEN_PERMISSIONS:
            session.execute(f"""
                INSERT INTO iam_permissions (
                    uuid, name, description
                ) VALUES (
                    '{_u(name)}',
                    '{name}',
                    '{description}'
                ) ON CONFLICT (uuid) DO NOTHING;
            """)
        for name, _ in SERVICE_TOKEN_PERMISSIONS:
            session.execute(f"""
                INSERT INTO iam_binding_permissions (
                    uuid, role, permission, project_id
                ) VALUES (
                    gen_random_uuid(),
                    '{OWNER_ROLE_UUID}',
                    '{_u(name)}',
                    NULL
                );
            """)

    def _downgrade_service_token(self, session):
        for name, _ in SERVICE_TOKEN_PERMISSIONS:
            session.execute(f"""
                DELETE FROM iam_binding_permissions
                WHERE permission = '{_u(name)}';
            """)
        for name, _ in SERVICE_TOKEN_PERMISSIONS:
            session.execute(f"""
                DELETE FROM iam_permissions
                WHERE uuid = '{_u(name)}';
            """)

    # ------------------------------------------------------------------
    # IAM user create permission (m0056)
    # ------------------------------------------------------------------

    def _upgrade_iam_user_create(self, session):
        for name, description in IAM_USER_PERMISSIONS:
            session.execute(f"""
                INSERT INTO iam_permissions (
                    uuid, name, description
                ) VALUES (
                    '{_u(name)}',
                    '{name}',
                    '{description}'
                ) ON CONFLICT (uuid) DO NOTHING;
            """)

    def _downgrade_iam_user_create(self, session):
        for name, _ in IAM_USER_PERMISSIONS:
            session.execute(f"""
                DELETE FROM iam_binding_permissions
                WHERE permission = '{_u(name)}';
            """)
        for name, _ in IAM_USER_PERMISSIONS:
            session.execute(f"""
                DELETE FROM iam_permissions
                WHERE uuid = '{_u(name)}';
            """)

    def _upgrade_actual_resources(self, session):
        expressions = [
            """
            INSERT INTO ua_actual_resources (
                uuid,
                kind,
                res_uuid,
                value,
                status,
                node,
                hash,
                full_hash,
                created_at,
                updated_at
            )
            SELECT
                uuid,
                kind,
                res_uuid,
                value,
                status,
                node,
                hash,
                full_hash,
                created_at,
                updated_at
            FROM ua_target_resources
            WHERE kind = 'em_core_iam_users'
            ON CONFLICT (res_uuid) DO NOTHING;
            """,
        ]

        for expression in expressions:
            session.execute(expression)

    def _downgrade_actual_resources(self, session):
        expressions = [
            """
            DELETE FROM ua_actual_resources
            WHERE kind = 'em_core_iam_users';
            """,
        ]

        for expression in expressions:
            session.execute(expression)


migration_step = MigrationStep()
