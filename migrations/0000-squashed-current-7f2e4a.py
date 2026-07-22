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


SCHEMA_STATEMENTS = (
    "CREATE TYPE public.enum_agent_status AS ENUM (\n    'NEW',\n    'ACTIVE',\n    'ERROR',\n    'DISABLED'\n)",
    "CREATE TYPE public.enum_config_status AS ENUM (\n    'NEW',\n    'IN_PROGRESS',\n    'ACTIVE',\n    'ERROR'\n)",
    "CREATE TYPE public.enum_secret_status AS ENUM (\n    'NEW',\n    'IN_PROGRESS',\n    'ACTIVE',\n    'ERROR'\n)",
    "CREATE TYPE public.enum_service_status AS ENUM (\n    'NEW',\n    'IN_PROGRESS',\n    'ACTIVE',\n    'ERROR'\n)",
    "CREATE TYPE public.enum_service_target_status AS ENUM (\n    'enabled',\n    'disabled'\n)",
    "CREATE TYPE public.user_type_enum AS ENUM (\n    'user',\n    'service'\n)",
    "CREATE SEQUENCE IF NOT EXISTS public.dns_domain_id_seq\n    START WITH 1\n    INCREMENT BY 1\n    NO MINVALUE\n    NO MAXVALUE\n    CACHE 1",
    "CREATE SEQUENCE IF NOT EXISTS public.dns_domainmetadata_id_seq\n    AS integer\n    START WITH 1\n    INCREMENT BY 1\n    NO MINVALUE\n    NO MAXVALUE\n    CACHE 1",
    "CREATE TABLE public.compute_net_interfaces (\n    uuid uuid NOT NULL,\n    name character varying(255) NOT NULL,\n    description character varying(255) NOT NULL,\n    machine uuid,\n    ipv4 character varying(15) DEFAULT NULL::character varying,\n    mask character varying(15) DEFAULT NULL::character varying,\n    mac character varying(17) DEFAULT NULL::character varying,\n    mtu integer DEFAULT 1500,\n    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL\n)",
    "CREATE TABLE public.compute_ports (\n    uuid uuid NOT NULL,\n    name character varying(255) NOT NULL,\n    description character varying(255) NOT NULL,\n    project_id uuid NOT NULL,\n    subnet uuid,\n    node uuid,\n    machine uuid,\n    interface character varying(32) DEFAULT NULL::character varying,\n    target_ipv4 character varying(15) DEFAULT NULL::character varying,\n    target_mask character varying(15) DEFAULT NULL::character varying,\n    ipv4 character varying(15) DEFAULT NULL::character varying,\n    mask character varying(15) DEFAULT NULL::character varying,\n    mac character varying(17) DEFAULT NULL::character varying,\n    status character varying(32) NOT NULL,\n    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    source character varying(128) DEFAULT NULL::character varying,\n    CONSTRAINT compute_ports_status_check CHECK (((status)::text = ANY ((ARRAY['NEW'::character varying, 'IN_PROGRESS'::character varying, 'ACTIVE'::character varying, 'ERROR'::character varying])::text[])))\n)",
    "CREATE TABLE public.machines (\n    uuid uuid NOT NULL,\n    project_id uuid NOT NULL,\n    name character varying(255) NOT NULL,\n    description character varying(255) NOT NULL,\n    cores integer NOT NULL,\n    ram integer NOT NULL,\n    node uuid,\n    machine_type character varying(2) NOT NULL,\n    boot character varying(8) NOT NULL,\n    pool uuid,\n    firmware_uuid uuid,\n    status character varying(32) NOT NULL,\n    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    image character varying(255) DEFAULT NULL::character varying,\n    block_devices jsonb DEFAULT '{}'::jsonb,\n    CONSTRAINT machines_boot_check CHECK (((boot)::text = ANY ((ARRAY['hd0'::character varying, 'hd1'::character varying, 'hd2'::character varying, 'hd3'::character varying, 'cdrom'::character varying, 'network'::character varying])::text[]))),\n    CONSTRAINT machines_machine_type_check CHECK (((machine_type)::text = ANY ((ARRAY['VM'::character varying, 'HW'::character varying])::text[]))),\n    CONSTRAINT machines_status_check CHECK (((status)::text = ANY ((ARRAY['NEW'::character varying, 'SCHEDULED'::character varying, 'IN_PROGRESS'::character varying, 'STARTED'::character varying, 'ACTIVE'::character varying, 'IDLE'::character varying, 'ERROR'::character varying])::text[])))\n)",
    "CREATE TABLE public.nodes (\n    uuid uuid NOT NULL,\n    project_id uuid NOT NULL,\n    name character varying(255) NOT NULL,\n    description character varying(255) NOT NULL,\n    cores integer NOT NULL,\n    ram integer NOT NULL,\n    node_type character varying(2) NOT NULL,\n    status character varying(32) NOT NULL,\n    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    default_network character varying(255) DEFAULT NULL::character varying,\n    node_set uuid,\n    placement_policies uuid[] DEFAULT '{}'::uuid[] NOT NULL,\n    disk_spec jsonb,\n    hostname character varying(256) DEFAULT NULL::character varying,\n    CONSTRAINT nodes_node_type_check CHECK (((node_type)::text = ANY ((ARRAY['VM'::character varying, 'HW'::character varying])::text[]))),\n    CONSTRAINT nodes_status_check CHECK (((status)::text = ANY ((ARRAY['NEW'::character varying, 'SCHEDULED'::character varying, 'IN_PROGRESS'::character varying, 'STARTED'::character varying, 'ACTIVE'::character varying, 'ERROR'::character varying])::text[])))\n)",
    "CREATE TABLE public.compute_machine_volumes (\n    uuid uuid NOT NULL,\n    project_id uuid NOT NULL,\n    name character varying(255) NOT NULL,\n    description character varying(255) NOT NULL,\n    node_volume uuid,\n    pool uuid,\n    machine uuid,\n    size integer NOT NULL,\n    boot boolean DEFAULT true NOT NULL,\n    index integer DEFAULT 4096 NOT NULL,\n    label character varying(256),\n    image character varying(256) DEFAULT NULL::character varying,\n    device_type character varying(64) DEFAULT ''::character varying NOT NULL,\n    status character varying(32) DEFAULT 'NEW'::character varying NOT NULL,\n    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    CONSTRAINT compute_machine_volumes_status_check CHECK (((status)::text = ANY ((ARRAY['NEW'::character varying, 'IN_PROGRESS'::character varying, 'ACTIVE'::character varying, 'ERROR'::character varying])::text[])))\n)",
    "CREATE TABLE public.compute_networks (\n    uuid uuid NOT NULL,\n    name character varying(255) NOT NULL,\n    description character varying(255) NOT NULL,\n    project_id uuid NOT NULL,\n    driver_spec jsonb DEFAULT '{}'::jsonb NOT NULL,\n    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL\n)",
    "CREATE TABLE public.compute_placement_domains (\n    uuid uuid NOT NULL,\n    name character varying(255) NOT NULL,\n    description character varying(255) NOT NULL,\n    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL\n)",
    "CREATE TABLE public.compute_placement_policies (\n    uuid uuid NOT NULL,\n    name character varying(255) NOT NULL,\n    description character varying(255) NOT NULL,\n    project_id uuid NOT NULL,\n    domain uuid,\n    zone uuid,\n    kind character varying(64) NOT NULL,\n    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL\n)",
    "CREATE TABLE public.compute_placement_policy_allocations (\n    uuid uuid NOT NULL,\n    node uuid,\n    policy uuid,\n    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL\n)",
    "CREATE TABLE public.compute_placement_zones (\n    uuid uuid NOT NULL,\n    name character varying(255) NOT NULL,\n    description character varying(255) NOT NULL,\n    domain uuid,\n    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL\n)",
    "CREATE TABLE public.compute_sets (\n    uuid uuid NOT NULL,\n    project_id uuid NOT NULL,\n    name character varying(255) NOT NULL,\n    description character varying(255) NOT NULL,\n    cores integer NOT NULL,\n    ram integer NOT NULL,\n    replicas integer NOT NULL,\n    node_type character varying(2) NOT NULL,\n    set_type character varying(32) NOT NULL,\n    status character varying(32) NOT NULL,\n    nodes jsonb NOT NULL,\n    default_network jsonb NOT NULL,\n    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    disk_spec jsonb,\n    CONSTRAINT compute_sets_node_type_check CHECK (((node_type)::text = ANY ((ARRAY['VM'::character varying, 'HW'::character varying])::text[]))),\n    CONSTRAINT compute_sets_set_type_check CHECK (((set_type)::text = 'SET'::text)),\n    CONSTRAINT compute_sets_status_check CHECK (((status)::text = ANY ((ARRAY['NEW'::character varying, 'SCHEDULED'::character varying, 'IN_PROGRESS'::character varying, 'STARTED'::character varying, 'ACTIVE'::character varying, 'ERROR'::character varying])::text[])))\n)",
    "CREATE TABLE public.compute_subnets (\n    uuid uuid NOT NULL,\n    name character varying(255) NOT NULL,\n    description character varying(255) NOT NULL,\n    project_id uuid NOT NULL,\n    network uuid,\n    cidr character varying(18) NOT NULL,\n    ip_range character varying(31) DEFAULT NULL::character varying,\n    dhcp boolean DEFAULT true,\n    dns_servers character varying(512) DEFAULT '{}'::character varying NOT NULL,\n    routers character varying(512) DEFAULT '{}'::character varying NOT NULL,\n    next_server character varying(256) DEFAULT '127.0.0.1'::character varying,\n    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    ip_discovery_range character varying(31) DEFAULT NULL::character varying\n)",
    "CREATE TABLE public.node_volumes (\n    uuid uuid NOT NULL,\n    project_id uuid NOT NULL,\n    name character varying(255) NOT NULL,\n    description character varying(255) NOT NULL,\n    node uuid,\n    size integer NOT NULL,\n    boot boolean DEFAULT true NOT NULL,\n    label character varying(127),\n    device_type character varying(64) NOT NULL,\n    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    status character varying(32) DEFAULT 'NEW'::character varying NOT NULL,\n    index integer DEFAULT 4096,\n    pool uuid,\n    image character varying(256) DEFAULT NULL::character varying,\n    CONSTRAINT node_volumes_status_check CHECK (((status)::text = ANY ((ARRAY['NEW'::character varying, 'IN_PROGRESS'::character varying, 'ACTIVE'::character varying, 'ERROR'::character varying])::text[])))\n)",
    "CREATE TABLE public.config_configs (\n    uuid uuid NOT NULL,\n    name character varying(255) NOT NULL,\n    description character varying(255) NOT NULL,\n    project_id uuid NOT NULL,\n    status public.enum_config_status DEFAULT 'NEW'::public.enum_config_status NOT NULL,\n    path character varying(255) NOT NULL,\n    target jsonb NOT NULL,\n    body jsonb NOT NULL,\n    on_change jsonb NOT NULL,\n    mode character(4) NOT NULL,\n    owner character varying(128) NOT NULL,\n    \"group\" character varying(128) NOT NULL,\n    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL\n)",
    "CREATE TABLE public.dns_domainmetadata (\n    id integer NOT NULL,\n    domain_id integer,\n    kind character varying(32),\n    content text\n)",
    "CREATE TABLE public.dns_domains (\n    uuid uuid NOT NULL,\n    id integer DEFAULT nextval('public.dns_domain_id_seq'::regclass),\n    name character varying(255) NOT NULL,\n    master character varying(128) DEFAULT NULL::character varying,\n    last_check integer,\n    type text DEFAULT 'NATIVE'::text NOT NULL,\n    notified_serial bigint,\n    account character varying(40) DEFAULT NULL::character varying,\n    options text,\n    catalog text,\n    project_id uuid NOT NULL,\n    created_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    updated_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    sync_to_ecosystem boolean DEFAULT false NOT NULL,\n    CONSTRAINT c_lowercase_name CHECK (((name)::text = lower((name)::text)))\n)",
    "CREATE TABLE public.dns_records (\n    uuid uuid NOT NULL,\n    domain_id integer,\n    name character varying(255) DEFAULT NULL::character varying,\n    type character varying(10) DEFAULT NULL::character varying,\n    content character varying(65535) DEFAULT NULL::character varying,\n    ttl integer,\n    prio integer,\n    disabled boolean DEFAULT false,\n    ordername character varying(255),\n    auth boolean DEFAULT true,\n    domain uuid NOT NULL,\n    record jsonb NOT NULL,\n    created_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    updated_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    project_id uuid NOT NULL,\n    CONSTRAINT dns_records_name_check CHECK (((name)::text = lower((name)::text)))\n)",
    "CREATE TABLE public.em_elements (\n    uuid uuid NOT NULL,\n    name character varying(255) NOT NULL,\n    description character varying(255) DEFAULT ''::character varying NOT NULL,\n    status character varying(20) DEFAULT 'NEW'::character varying NOT NULL,\n    version character varying(64) NOT NULL,\n    install_type character varying(20) DEFAULT 'MANUAL'::character varying NOT NULL,\n    created_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    updated_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    api_version character varying(16) DEFAULT NULL::character varying,\n    profile uuid,\n    project_id uuid,\n    requirements jsonb DEFAULT '{}'::jsonb NOT NULL,\n    manifest uuid,\n    CONSTRAINT em_elements_install_type_check CHECK (((install_type)::text = ANY ((ARRAY['MANUAL'::character varying, 'AUTO_AS_DEPENDENCY'::character varying])::text[]))),\n    CONSTRAINT em_elements_status_check CHECK (((status)::text = ANY ((ARRAY['NEW'::character varying, 'IN_PROGRESS'::character varying, 'ACTIVE'::character varying])::text[])))\n)",
    "CREATE TABLE public.em_exports (\n    uuid uuid NOT NULL,\n    element uuid NOT NULL,\n    name character varying(255) NOT NULL,\n    kind character varying(20) DEFAULT 'resource'::character varying NOT NULL,\n    link character varying(255) NOT NULL,\n    created_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    updated_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    CONSTRAINT em_exports_kind_check CHECK (((kind)::text = 'resource'::text))\n)",
    "CREATE TABLE public.em_imports (\n    uuid uuid NOT NULL,\n    element uuid NOT NULL,\n    from_element uuid NOT NULL,\n    from_resource uuid NOT NULL,\n    name character varying(255) NOT NULL,\n    kind character varying(20) DEFAULT 'resource'::character varying NOT NULL,\n    created_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    updated_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    CONSTRAINT em_imports_kind_check CHECK (((kind)::text = 'resource'::text))\n)",
    "CREATE TABLE public.em_resources (\n    uuid uuid NOT NULL,\n    name character varying(255) NOT NULL,\n    element uuid NOT NULL,\n    status character varying(20) DEFAULT 'NEW'::character varying NOT NULL,\n    resource_link_prefix character varying(256) NOT NULL,\n    value jsonb DEFAULT '{}'::jsonb NOT NULL,\n    target_resource uuid,\n    actual_resource uuid,\n    full_hash character varying(256) DEFAULT ''::character varying NOT NULL,\n    created_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    updated_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    CONSTRAINT em_resources_status_check CHECK (((status)::text = ANY ((ARRAY['NEW'::character varying, 'IN_PROGRESS'::character varying, 'ACTIVE'::character varying])::text[])))\n)",
    "CREATE TABLE public.em_manifests (\n    uuid uuid NOT NULL,\n    name character varying(255) NOT NULL,\n    description character varying(255) DEFAULT ''::character varying NOT NULL,\n    status character varying(20) DEFAULT 'NEW'::character varying NOT NULL,\n    version character varying(64) NOT NULL,\n    schema_version integer DEFAULT 1 NOT NULL,\n    project_id uuid NOT NULL,\n    requirements jsonb DEFAULT '{}'::jsonb NOT NULL,\n    resources jsonb DEFAULT '{}'::jsonb NOT NULL,\n    created_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    updated_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    api_version character varying(16) DEFAULT NULL::character varying,\n    exports jsonb DEFAULT '{}'::jsonb NOT NULL,\n    imports jsonb DEFAULT '{}'::jsonb NOT NULL,\n    openapi_spec text,\n    CONSTRAINT em_manifests_status_check CHECK (((status)::text = 'ACTIVE'::text))\n)",
    "CREATE TABLE public.em_services (\n    uuid uuid NOT NULL,\n    name character varying(255) NOT NULL,\n    description character varying(255) NOT NULL,\n    project_id uuid NOT NULL,\n    status public.enum_service_status DEFAULT 'NEW'::public.enum_service_status NOT NULL,\n    target_status public.enum_service_target_status DEFAULT 'enabled'::public.enum_service_target_status NOT NULL,\n    path character varying(255) NOT NULL,\n    target jsonb NOT NULL,\n    service_type jsonb NOT NULL,\n    before jsonb[],\n    after jsonb[],\n    \"user\" character varying(255) NOT NULL,\n    \"group\" character varying(255),\n    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL\n)",
    "CREATE TABLE public.gcl_sdk_audit_logs (\n    uuid uuid NOT NULL,\n    object_uuid uuid NOT NULL,\n    object_type character varying(64) NOT NULL,\n    user_uuid uuid,\n    action character varying(64) NOT NULL,\n    created_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    updated_at timestamp(6) without time zone DEFAULT now() NOT NULL\n)",
    "CREATE TABLE public.gcl_sdk_events (\n    uuid uuid NOT NULL,\n    status character varying(20) DEFAULT 'NEW'::character varying NOT NULL,\n    event_type jsonb NOT NULL,\n    event_data jsonb NOT NULL,\n    created_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    updated_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    CONSTRAINT gcl_sdk_events_status_check CHECK (((status)::text = ANY ((ARRAY['NEW'::character varying, 'IN_PROGRESS'::character varying, 'ERROR'::character varying, 'ACTIVE'::character varying])::text[])))\n)",
    "CREATE TABLE public.iam_binding_permissions (\n    uuid uuid NOT NULL,\n    status character varying(20) DEFAULT 'ACTIVE'::character varying NOT NULL,\n    project_id uuid,\n    role uuid NOT NULL,\n    permission uuid NOT NULL,\n    description character varying(256) DEFAULT ''::character varying,\n    created_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    updated_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    CONSTRAINT iam_binding_permissions_status_check CHECK (((status)::text = 'ACTIVE'::text))\n)",
    "CREATE TABLE public.iam_binding_roles (\n    uuid uuid NOT NULL,\n    status character varying(20) DEFAULT 'ACTIVE'::character varying NOT NULL,\n    \"user\" uuid NOT NULL,\n    role uuid NOT NULL,\n    project uuid,\n    description character varying(256) DEFAULT ''::character varying,\n    created_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    updated_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    CONSTRAINT iam_binding_roles_status_check CHECK (((status)::text = 'ACTIVE'::text))\n)",
    'CREATE TABLE public.iam_clients (\n    uuid uuid NOT NULL,\n    status character varying(20) DEFAULT \'ACTIVE\'::character varying NOT NULL,\n    name character varying(256) NOT NULL,\n    project_id uuid,\n    description character varying(256) DEFAULT \'\'::character varying,\n    client_id character varying(64) NOT NULL,\n    secret_hash character(128) NOT NULL,\n    salt character(24) NOT NULL,\n    created_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    updated_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    signature_algorithm jsonb DEFAULT \'{"kind": "HS256", "secret_uuid": "00000000-0000-0000-0000-000000000001", "previous_secret_uuid": null}\'::jsonb NOT NULL,\n    registration_auto_provision boolean DEFAULT true NOT NULL,\n    CONSTRAINT iam_clients_status_check CHECK (((status)::text = \'ACTIVE\'::text))\n)',
    "CREATE TABLE public.iam_idp (\n    uuid uuid NOT NULL,\n    status character varying(20) DEFAULT 'ACTIVE'::character varying NOT NULL,\n    name character varying(256) NOT NULL,\n    project_id uuid,\n    description character varying(256) DEFAULT ''::character varying,\n    scope character varying(64) DEFAULT 'openid'::character varying,\n    created_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    updated_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    iam_client uuid,\n    nonce_required boolean DEFAULT true NOT NULL,\n    callback jsonb DEFAULT '{\"kind\": \"callback_uri\", \"callback\": \"\"}'::jsonb NOT NULL,\n    CONSTRAINT iam_idp_status_check CHECK (((status)::text = 'ACTIVE'::text))\n)",
    "CREATE TABLE public.iam_idp_authorization_info (\n    uuid uuid NOT NULL,\n    idp uuid NOT NULL,\n    state character varying(256) NOT NULL,\n    response_type character varying(20) DEFAULT 'code'::character varying NOT NULL,\n    nonce character varying(256) NOT NULL,\n    scope character varying(256) NOT NULL,\n    expiration_time_at timestamp(6) without time zone NOT NULL,\n    token uuid,\n    code uuid NOT NULL,\n    created_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    updated_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    redirect_uri character varying(256) DEFAULT ''::character varying NOT NULL,\n    CONSTRAINT iam_idp_authorization_info_response_type_check CHECK (((response_type)::text = 'code'::text))\n)",
    "CREATE TABLE public.iam_organization_members (\n    uuid uuid NOT NULL,\n    organization uuid NOT NULL,\n    \"user\" uuid NOT NULL,\n    role character varying(20) DEFAULT 'MEMBER'::character varying NOT NULL,\n    created_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    updated_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    CONSTRAINT iam_organization_members_role_check CHECK (((role)::text = ANY ((ARRAY['OWNER'::character varying, 'MEMBER'::character varying])::text[])))\n)",
    "CREATE TABLE public.iam_organizations (\n    uuid uuid NOT NULL,\n    status character varying(20) DEFAULT 'ACTIVE'::character varying NOT NULL,\n    name character varying(128) NOT NULL,\n    description character varying(256) DEFAULT ''::character varying,\n    created_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    updated_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    info character varying(2048) DEFAULT '{}'::character varying,\n    CONSTRAINT iam_organizations_status_check CHECK (((status)::text = 'ACTIVE'::text))\n)",
    "CREATE TABLE public.iam_permissions (\n    uuid uuid NOT NULL,\n    status character varying(20) DEFAULT 'ACTIVE'::character varying NOT NULL,\n    name character varying(256) NOT NULL,\n    description character varying(256) DEFAULT ''::character varying,\n    created_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    updated_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    CONSTRAINT iam_permissions_status_check CHECK (((status)::text = 'ACTIVE'::text))\n)",
    "CREATE TABLE public.iam_users (\n    uuid uuid NOT NULL,\n    status character varying(20) DEFAULT 'ACTIVE'::character varying NOT NULL,\n    name character varying(256) NOT NULL,\n    description character varying(256) NOT NULL,\n    first_name character varying(128),\n    last_name character varying(128),\n    email character varying(128) NOT NULL,\n    secret_hash character(128) NOT NULL,\n    salt character(24) NOT NULL,\n    otp_secret character varying(128) DEFAULT ''::character varying,\n    otp_enabled boolean DEFAULT false,\n    created_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    updated_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    surname character varying(128) DEFAULT ''::character varying NOT NULL,\n    phone character varying(15),\n    email_verified boolean DEFAULT false NOT NULL,\n    confirmation_code uuid,\n    confirmation_code_made_at timestamp without time zone,\n    user_source jsonb DEFAULT '{\"kind\": \"IAM\"}'::jsonb NOT NULL,\n    custom_props jsonb,\n    type public.user_type_enum DEFAULT 'user'::public.user_type_enum NOT NULL,\n    registration_client uuid,\n    CONSTRAINT iam_users_status_check CHECK (((status)::text = 'ACTIVE'::text))\n)",
    "CREATE TABLE public.iam_projects (\n    uuid uuid NOT NULL,\n    status character varying(20) DEFAULT 'ACTIVE'::character varying NOT NULL,\n    name character varying(128) NOT NULL,\n    description character varying(256) DEFAULT ''::character varying,\n    organization uuid NOT NULL,\n    created_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    updated_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    CONSTRAINT iam_projects_status_check CHECK (((status)::text = ANY ((ARRAY['NEW'::character varying, 'IN_PROGRESS'::character varying, 'ACTIVE'::character varying, 'DELETING'::character varying])::text[])))\n)",
    "CREATE TABLE public.iam_roles (\n    uuid uuid NOT NULL,\n    status character varying(20) DEFAULT 'ACTIVE'::character varying NOT NULL,\n    name character varying(128) NOT NULL,\n    description character varying(256) DEFAULT ''::character varying,\n    project_id uuid,\n    created_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    updated_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    CONSTRAINT iam_roles_status_check CHECK (((status)::text = 'ACTIVE'::text))\n)",
    "CREATE TABLE public.iam_tokens (\n    uuid uuid NOT NULL,\n    \"user\" uuid NOT NULL,\n    project uuid,\n    expiration_at timestamp(6) without time zone CONSTRAINT iam_tokens_experation_at_not_null NOT NULL,\n    refresh_token_uuid uuid NOT NULL,\n    refresh_expiration_at timestamp(6) without time zone CONSTRAINT iam_tokens_refresh_experation_at_not_null NOT NULL,\n    issuer character varying(256) DEFAULT NULL::character varying,\n    audience character varying(256) DEFAULT 'account'::character varying,\n    typ character varying(64) DEFAULT 'Bearer'::character varying,\n    scope character varying(128) NOT NULL,\n    created_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    updated_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    expiration_delta double precision DEFAULT 900.0 NOT NULL,\n    refresh_expiration_delta double precision DEFAULT 86400.0 NOT NULL,\n    nonce character varying(256) DEFAULT NULL::character varying,\n    iam_client uuid NOT NULL\n)",
    "CREATE TABLE public.machine_pools (\n    uuid uuid NOT NULL,\n    name character varying(255) NOT NULL,\n    description character varying(255) NOT NULL,\n    driver_spec jsonb NOT NULL,\n    machine_type character varying(2) NOT NULL,\n    status character varying(32) NOT NULL,\n    avail_cores integer DEFAULT 0 NOT NULL,\n    avail_ram integer DEFAULT 0 NOT NULL,\n    all_cores integer DEFAULT 0 NOT NULL,\n    all_ram integer DEFAULT 0 NOT NULL,\n    storage_pools jsonb[] DEFAULT '{}'::jsonb[],\n    builder uuid,\n    agent uuid,\n    cores_ratio double precision DEFAULT 1.0 NOT NULL,\n    ram_ratio double precision DEFAULT 1.0 NOT NULL,\n    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    CONSTRAINT machine_pools_machine_type_check CHECK (((machine_type)::text = ANY ((ARRAY['VM'::character varying, 'HW'::character varying])::text[]))),\n    CONSTRAINT machine_pools_status_check CHECK (((status)::text = ANY ((ARRAY['ACTIVE'::character varying, 'DISABLED'::character varying, 'MAINTENANCE'::character varying, 'IN_PROGRESS'::character varying])::text[])))\n)",
    "CREATE TABLE public.n_builders (\n    uuid uuid NOT NULL,\n    status character varying(32) NOT NULL,\n    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    CONSTRAINT n_builders_status_check CHECK (((status)::text = ANY ((ARRAY['ACTIVE'::character varying, 'DISABLED'::character varying])::text[])))\n)",
    "CREATE TABLE public.n_machine_pool_reservations (\n    uuid uuid NOT NULL,\n    cores integer NOT NULL,\n    ram integer NOT NULL,\n    pool uuid,\n    machine uuid,\n    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL\n)",
    "CREATE TABLE public.net_border (\n    uuid uuid NOT NULL,\n    name character varying(255) NOT NULL,\n    description text,\n    project_id uuid NOT NULL,\n    status character varying(64) DEFAULT 'NEW'::character varying NOT NULL,\n    created_at timestamp without time zone NOT NULL,\n    updated_at timestamp without time zone NOT NULL,\n    node uuid,\n    snat_rules jsonb[] DEFAULT '{}'::jsonb[] NOT NULL,\n    forwards jsonb[] DEFAULT '{}'::jsonb[] NOT NULL,\n    type jsonb DEFAULT '{\"kind\": \"core_agent\"}'::jsonb NOT NULL,\n    ipsv4 character varying(15)[] DEFAULT '{}'::character varying[] NOT NULL\n)",
    "CREATE TABLE public.net_lb (\n    uuid uuid NOT NULL,\n    name character varying(255) NOT NULL,\n    description text,\n    project_id uuid NOT NULL,\n    status character varying(64) DEFAULT 'NEW'::character varying NOT NULL,\n    ipsv4 character varying(15)[],\n    type jsonb NOT NULL,\n    created_at timestamp without time zone NOT NULL,\n    updated_at timestamp without time zone NOT NULL\n)",
    "CREATE TABLE public.net_lb_backendpools (\n    uuid uuid NOT NULL,\n    name character varying(64) NOT NULL,\n    status character varying(64) DEFAULT 'NEW'::character varying NOT NULL,\n    description text,\n    project_id uuid NOT NULL,\n    created_at timestamp without time zone NOT NULL,\n    updated_at timestamp without time zone NOT NULL,\n    endpoints jsonb[] NOT NULL,\n    balance character varying(32) NOT NULL,\n    parent uuid NOT NULL\n)",
    "CREATE TABLE public.net_lb_vhosts (\n    uuid uuid NOT NULL,\n    name character varying(64) NOT NULL,\n    enabled boolean DEFAULT true NOT NULL,\n    status character varying(64) DEFAULT 'NEW'::character varying NOT NULL,\n    description text,\n    project_id uuid NOT NULL,\n    created_at timestamp without time zone NOT NULL,\n    updated_at timestamp without time zone NOT NULL,\n    protocol character varying(10) NOT NULL,\n    port integer NOT NULL,\n    domains character varying(255)[],\n    cert jsonb,\n    parent uuid NOT NULL,\n    external_sources jsonb[] DEFAULT '{}'::jsonb[] NOT NULL,\n    proxy_protocol_from character varying(18)\n)",
    "CREATE TABLE public.net_lb_vhosts_routes (\n    uuid uuid NOT NULL,\n    name character varying(64) NOT NULL,\n    enabled boolean DEFAULT true NOT NULL,\n    status character varying(64) DEFAULT 'NEW'::character varying NOT NULL,\n    description text,\n    project_id uuid NOT NULL,\n    created_at timestamp without time zone NOT NULL,\n    updated_at timestamp without time zone NOT NULL,\n    condition jsonb,\n    parent uuid NOT NULL\n)",
    "CREATE TABLE public.repo_artifacts (\n    uuid uuid NOT NULL,\n    project_id uuid NOT NULL,\n    element uuid NOT NULL,\n    urn character varying(2048) NOT NULL,\n    uri character varying(2048) NOT NULL\n)",
    "CREATE TABLE public.repo_element_deps_bindings (\n    uuid uuid NOT NULL,\n    element uuid NOT NULL,\n    depends_on uuid NOT NULL\n)",
    "CREATE TABLE public.repo_elements (\n    uuid uuid NOT NULL,\n    name character varying(255) NOT NULL,\n    description text DEFAULT ''::text NOT NULL,\n    project_id uuid NOT NULL,\n    repository uuid NOT NULL,\n    version character varying(255) NOT NULL,\n    status character varying(32) DEFAULT 'NEW'::character varying NOT NULL,\n    installation_state character varying(32) DEFAULT 'UNINSTALLED'::character varying NOT NULL,\n    manifest jsonb DEFAULT '{}'::jsonb,\n    specification jsonb DEFAULT '{}'::jsonb,\n    inventory jsonb DEFAULT '{}'::jsonb,\n    element uuid,\n    created_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    updated_at timestamp(6) without time zone DEFAULT now() NOT NULL\n)",
    "CREATE TABLE public.repo_repositories (\n    uuid uuid NOT NULL,\n    name character varying(255) NOT NULL,\n    description text DEFAULT ''::text NOT NULL,\n    project_id uuid NOT NULL,\n    status character varying(32) DEFAULT 'NEW'::character varying NOT NULL,\n    priority integer DEFAULT 2048 NOT NULL,\n    refresh_rate integer DEFAULT 3600 NOT NULL,\n    sync_mode character varying(32) DEFAULT 'copy'::character varying NOT NULL,\n    driver_spec jsonb,\n    next_refresh timestamp(6) without time zone DEFAULT now() NOT NULL,\n    created_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    updated_at timestamp(6) without time zone DEFAULT now() NOT NULL\n)",
    "CREATE TABLE public.secret_certificates (\n    uuid uuid NOT NULL,\n    name character varying(255) NOT NULL,\n    description character varying(255) NOT NULL,\n    project_id uuid NOT NULL,\n    status character varying(32) NOT NULL,\n    constructor jsonb NOT NULL,\n    method jsonb NOT NULL,\n    email character varying(254) NOT NULL,\n    domains character varying(1024) NOT NULL,\n    key text,\n    cert text,\n    expiration_at timestamp without time zone,\n    expiration_threshold integer NOT NULL,\n    overcome_threshold boolean DEFAULT false,\n    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    CONSTRAINT secret_certificates_status_check CHECK (((status)::text = ANY ((ARRAY['NEW'::character varying, 'IN_PROGRESS'::character varying, 'ACTIVE'::character varying, 'ERROR'::character varying])::text[])))\n)",
    "CREATE TABLE public.secret_passwords (\n    uuid uuid NOT NULL,\n    name character varying(255) NOT NULL,\n    description character varying(255) NOT NULL,\n    project_id uuid NOT NULL,\n    status public.enum_secret_status DEFAULT 'NEW'::public.enum_secret_status NOT NULL,\n    constructor jsonb NOT NULL,\n    value character varying(512) DEFAULT NULL::character varying,\n    method character varying(64) NOT NULL,\n    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    default_length integer DEFAULT 32 NOT NULL\n)",
    "CREATE TABLE public.secret_rsa_keys (\n    uuid uuid NOT NULL,\n    name character varying(255) NOT NULL,\n    description character varying(255) NOT NULL,\n    project_id uuid NOT NULL,\n    status character varying(32) NOT NULL,\n    constructor jsonb NOT NULL,\n    private_key text NOT NULL,\n    public_key text NOT NULL,\n    bitness integer DEFAULT 2048 NOT NULL,\n    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    CONSTRAINT secret_rsa_keys_bitness_check CHECK ((bitness = ANY (ARRAY[2048, 3072, 4096]))),\n    CONSTRAINT secret_rsa_keys_status_check CHECK (((status)::text = ANY ((ARRAY['NEW'::character varying, 'IN_PROGRESS'::character varying, 'ACTIVE'::character varying, 'ERROR'::character varying])::text[])))\n)",
    "CREATE TABLE public.secret_ssh_keys (\n    uuid uuid NOT NULL,\n    name character varying(255) NOT NULL,\n    description character varying(255) NOT NULL,\n    project_id uuid NOT NULL,\n    status character varying(32) NOT NULL,\n    constructor jsonb NOT NULL,\n    target jsonb NOT NULL,\n    \"user\" character varying(64) NOT NULL,\n    authorized_keys character varying(256) NOT NULL,\n    target_public_key text,\n    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    CONSTRAINT secret_ssh_keys_status_check CHECK (((status)::text = ANY ((ARRAY['NEW'::character varying, 'IN_PROGRESS'::character varying, 'ACTIVE'::character varying, 'ERROR'::character varying])::text[])))\n)",
    "CREATE TABLE public.security_rules (\n    uuid uuid NOT NULL,\n    name character varying(255) NOT NULL,\n    description character varying(255) DEFAULT ''::character varying NOT NULL,\n    project_id uuid,\n    condition jsonb NOT NULL,\n    action jsonb CONSTRAINT security_rules_verifier_not_null NOT NULL,\n    operator character varying(8) DEFAULT 'OR'::character varying NOT NULL,\n    status character varying(20) DEFAULT 'ACTIVE'::character varying NOT NULL,\n    created_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    updated_at timestamp(6) without time zone DEFAULT now() NOT NULL,\n    CONSTRAINT security_rules_operator_check CHECK (((operator)::text = ANY ((ARRAY['OR'::character varying, 'AND'::character varying])::text[]))),\n    CONSTRAINT security_rules_status_check CHECK (((status)::text = 'ACTIVE'::text))\n)",
    "CREATE TABLE public.storage_certs (\n    uuid uuid NOT NULL,\n    status character varying(32) NOT NULL,\n    pkey character varying(10240) NOT NULL,\n    fullchain character varying(10240) NOT NULL,\n    csr character varying(10240) NOT NULL,\n    expiration_at timestamp without time zone NOT NULL,\n    meta jsonb NOT NULL,\n    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    CONSTRAINT storage_certs_status_check CHECK (((status)::text = ANY ((ARRAY['NEW'::character varying, 'IN_PROGRESS'::character varying, 'ACTIVE'::character varying, 'ERROR'::character varying])::text[])))\n)",
    "CREATE TABLE public.storage_passwords (\n    uuid uuid NOT NULL,\n    status public.enum_secret_status DEFAULT 'NEW'::public.enum_secret_status NOT NULL,\n    value character varying(512) NOT NULL,\n    meta jsonb NOT NULL,\n    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL\n)",
    "CREATE TABLE public.vs_profiles (\n    uuid uuid NOT NULL,\n    name character varying(255) NOT NULL,\n    description character varying(255) NOT NULL,\n    project_id uuid NOT NULL,\n    status character varying(32) NOT NULL,\n    profile_type character varying(32) NOT NULL,\n    active boolean DEFAULT false,\n    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    CONSTRAINT vs_profiles_profile_type_check CHECK (((profile_type)::text = ANY ((ARRAY['GLOBAL'::character varying, 'ELEMENT'::character varying])::text[]))),\n    CONSTRAINT vs_profiles_status_check CHECK (((status)::text = ANY ((ARRAY['NEW'::character varying, 'IN_PROGRESS'::character varying, 'ACTIVE'::character varying, 'ERROR'::character varying])::text[])))\n)",
    "CREATE TABLE public.vs_values (\n    uuid uuid NOT NULL,\n    name character varying(255) NOT NULL,\n    description character varying(255) NOT NULL,\n    project_id uuid NOT NULL,\n    status character varying(32) NOT NULL,\n    value jsonb,\n    read_only boolean DEFAULT false,\n    manual_selected boolean DEFAULT false,\n    variable uuid,\n    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    CONSTRAINT vs_values_status_check CHECK (((status)::text = ANY ((ARRAY['NEW'::character varying, 'IN_PROGRESS'::character varying, 'ACTIVE'::character varying, 'ERROR'::character varying])::text[])))\n)",
    "CREATE TABLE public.vs_variables (\n    uuid uuid NOT NULL,\n    name character varying(255) NOT NULL,\n    description character varying(255) NOT NULL,\n    project_id uuid NOT NULL,\n    status character varying(32) NOT NULL,\n    setter jsonb NOT NULL,\n    value jsonb,\n    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,\n    CONSTRAINT vs_variables_status_check CHECK (((status)::text = ANY ((ARRAY['NEW'::character varying, 'IN_PROGRESS'::character varying, 'ACTIVE'::character varying, 'ERROR'::character varying])::text[])))\n)",
    "ALTER TABLE ONLY public.dns_domainmetadata ALTER COLUMN id SET DEFAULT nextval('public.dns_domainmetadata_id_seq'::regclass)",
    "ALTER TABLE ONLY public.compute_machine_volumes\n    ADD CONSTRAINT compute_machine_volumes_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.compute_net_interfaces\n    ADD CONSTRAINT compute_net_interfaces_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.compute_networks\n    ADD CONSTRAINT compute_networks_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.compute_placement_domains\n    ADD CONSTRAINT compute_placement_domains_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.compute_placement_policies\n    ADD CONSTRAINT compute_placement_policies_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.compute_placement_policy_allocations\n    ADD CONSTRAINT compute_placement_policy_allocations_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.compute_placement_zones\n    ADD CONSTRAINT compute_placement_zones_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.compute_ports\n    ADD CONSTRAINT compute_ports_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.compute_sets\n    ADD CONSTRAINT compute_sets_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.compute_subnets\n    ADD CONSTRAINT compute_subnets_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.config_configs\n    ADD CONSTRAINT config_configs_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.dns_domainmetadata\n    ADD CONSTRAINT dns_domainmetadata_pkey PRIMARY KEY (id)",
    "ALTER TABLE ONLY public.dns_domains\n    ADD CONSTRAINT dns_domains_id_key UNIQUE (id)",
    "ALTER TABLE ONLY public.dns_domains\n    ADD CONSTRAINT dns_domains_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.dns_records\n    ADD CONSTRAINT dns_records_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.em_elements\n    ADD CONSTRAINT em_elements_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.em_elements\n    ADD CONSTRAINT em_elements_unique_name UNIQUE (name)",
    "ALTER TABLE ONLY public.em_exports\n    ADD CONSTRAINT em_exports_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.em_exports\n    ADD CONSTRAINT em_exports_unique_name UNIQUE (element, name)",
    "ALTER TABLE ONLY public.em_imports\n    ADD CONSTRAINT em_imports_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.em_imports\n    ADD CONSTRAINT em_imports_unique_name UNIQUE (element, name)",
    "ALTER TABLE ONLY public.em_manifests\n    ADD CONSTRAINT em_manifests_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.em_resources\n    ADD CONSTRAINT em_resources_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.em_services\n    ADD CONSTRAINT em_services_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.gcl_sdk_audit_logs\n    ADD CONSTRAINT gcl_sdk_audit_logs_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.gcl_sdk_events\n    ADD CONSTRAINT gcl_sdk_events_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.iam_binding_permissions\n    ADD CONSTRAINT iam_binding_permissions_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.iam_binding_roles\n    ADD CONSTRAINT iam_binding_roles_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.iam_clients\n    ADD CONSTRAINT iam_clients_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.iam_idp_authorization_info\n    ADD CONSTRAINT iam_idp_authorization_info_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.iam_idp\n    ADD CONSTRAINT iam_idp_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.iam_organization_members\n    ADD CONSTRAINT iam_organization_members_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.iam_organizations\n    ADD CONSTRAINT iam_organizations_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.iam_permissions\n    ADD CONSTRAINT iam_permissions_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.iam_projects\n    ADD CONSTRAINT iam_projects_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.iam_roles\n    ADD CONSTRAINT iam_roles_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.iam_tokens\n    ADD CONSTRAINT iam_tokens_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.iam_users\n    ADD CONSTRAINT iam_users_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.machine_pools\n    ADD CONSTRAINT machine_pools_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.machines\n    ADD CONSTRAINT machines_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.n_builders\n    ADD CONSTRAINT n_builders_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.n_machine_pool_reservations\n    ADD CONSTRAINT n_machine_pool_reservations_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.net_border\n    ADD CONSTRAINT net_border_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.net_lb_backendpools\n    ADD CONSTRAINT net_lb_backendpools_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.net_lb\n    ADD CONSTRAINT net_lb_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.net_lb_vhosts\n    ADD CONSTRAINT net_lb_vhosts_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.net_lb_vhosts_routes\n    ADD CONSTRAINT net_lb_vhosts_routes_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.node_volumes\n    ADD CONSTRAINT node_volumes_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.nodes\n    ADD CONSTRAINT nodes_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.ra_migrations\n    ADD CONSTRAINT ra_migrations_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.repo_artifacts\n    ADD CONSTRAINT repo_artifacts_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.repo_element_deps_bindings\n    ADD CONSTRAINT repo_element_deps_bindings_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.repo_elements\n    ADD CONSTRAINT repo_elements_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.repo_repositories\n    ADD CONSTRAINT repo_repositories_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.secret_certificates\n    ADD CONSTRAINT secret_certificates_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.secret_passwords\n    ADD CONSTRAINT secret_passwords_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.secret_rsa_keys\n    ADD CONSTRAINT secret_rsa_keys_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.secret_ssh_keys\n    ADD CONSTRAINT secret_ssh_keys_pkey PRIMARY KEY (uuid)",
    'ALTER TABLE ONLY public.secret_ssh_keys\n    ADD CONSTRAINT secret_ssh_keys_user_target_target_public_key_key UNIQUE ("user", target, target_public_key)',
    "ALTER TABLE ONLY public.security_rules\n    ADD CONSTRAINT security_rules_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.storage_certs\n    ADD CONSTRAINT storage_certs_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.storage_passwords\n    ADD CONSTRAINT storage_passwords_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.em_elements\n    ADD CONSTRAINT unique_em_elements_name_version_idx UNIQUE (name, version)",
    'ALTER TABLE ONLY public.iam_organization_members\n    ADD CONSTRAINT uq_organization_user UNIQUE (organization, "user")',
    "ALTER TABLE ONLY public.vs_profiles\n    ADD CONSTRAINT vs_profiles_name_key UNIQUE (name)",
    "ALTER TABLE ONLY public.vs_profiles\n    ADD CONSTRAINT vs_profiles_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.vs_values\n    ADD CONSTRAINT vs_values_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.vs_variables\n    ADD CONSTRAINT vs_variables_pkey PRIMARY KEY (uuid)",
    "ALTER TABLE ONLY public.compute_machine_volumes\n    ADD CONSTRAINT compute_machine_volumes_machine_fkey FOREIGN KEY (machine) REFERENCES public.machines(uuid) ON DELETE SET NULL",
    "ALTER TABLE ONLY public.compute_machine_volumes\n    ADD CONSTRAINT compute_machine_volumes_node_volume_fkey FOREIGN KEY (node_volume) REFERENCES public.node_volumes(uuid) ON DELETE CASCADE",
    "ALTER TABLE ONLY public.compute_machine_volumes\n    ADD CONSTRAINT compute_machine_volumes_pool_fkey FOREIGN KEY (pool) REFERENCES public.machine_pools(uuid) ON DELETE SET NULL",
    "ALTER TABLE ONLY public.compute_net_interfaces\n    ADD CONSTRAINT compute_net_interfaces_machine_fkey FOREIGN KEY (machine) REFERENCES public.machines(uuid) ON DELETE CASCADE",
    "ALTER TABLE ONLY public.compute_placement_policies\n    ADD CONSTRAINT compute_placement_policies_domain_fkey FOREIGN KEY (domain) REFERENCES public.compute_placement_domains(uuid) ON DELETE RESTRICT",
    "ALTER TABLE ONLY public.compute_placement_policies\n    ADD CONSTRAINT compute_placement_policies_zone_fkey FOREIGN KEY (zone) REFERENCES public.compute_placement_zones(uuid) ON DELETE RESTRICT",
    "ALTER TABLE ONLY public.compute_placement_policy_allocations\n    ADD CONSTRAINT compute_placement_policy_allocations_node_fkey FOREIGN KEY (node) REFERENCES public.nodes(uuid) ON DELETE CASCADE",
    "ALTER TABLE ONLY public.compute_placement_policy_allocations\n    ADD CONSTRAINT compute_placement_policy_allocations_policy_fkey FOREIGN KEY (policy) REFERENCES public.compute_placement_policies(uuid) ON DELETE CASCADE",
    "ALTER TABLE ONLY public.compute_placement_zones\n    ADD CONSTRAINT compute_placement_zones_domain_fkey FOREIGN KEY (domain) REFERENCES public.compute_placement_domains(uuid) ON DELETE RESTRICT",
    "ALTER TABLE ONLY public.compute_ports\n    ADD CONSTRAINT compute_ports_machine_fkey FOREIGN KEY (machine) REFERENCES public.machines(uuid) ON DELETE CASCADE",
    "ALTER TABLE ONLY public.compute_ports\n    ADD CONSTRAINT compute_ports_node_fkey FOREIGN KEY (node) REFERENCES public.nodes(uuid) ON DELETE CASCADE",
    "ALTER TABLE ONLY public.compute_ports\n    ADD CONSTRAINT compute_ports_subnet_fkey FOREIGN KEY (subnet) REFERENCES public.compute_subnets(uuid) ON DELETE RESTRICT",
    "ALTER TABLE ONLY public.compute_subnets\n    ADD CONSTRAINT compute_subnets_network_fkey FOREIGN KEY (network) REFERENCES public.compute_networks(uuid) ON DELETE RESTRICT",
    "ALTER TABLE ONLY public.dns_domainmetadata\n    ADD CONSTRAINT dns_domainmetadata_domain_id_fkey FOREIGN KEY (domain_id) REFERENCES public.dns_domains(id) ON DELETE CASCADE",
    "ALTER TABLE ONLY public.dns_records\n    ADD CONSTRAINT dns_records_domain_fkey FOREIGN KEY (domain) REFERENCES public.dns_domains(uuid) ON DELETE CASCADE",
    "ALTER TABLE ONLY public.dns_records\n    ADD CONSTRAINT dns_records_domain_id_fkey FOREIGN KEY (domain_id) REFERENCES public.dns_domains(id) ON DELETE RESTRICT",
    "ALTER TABLE ONLY public.em_elements\n    ADD CONSTRAINT em_elements_manifest_fkey FOREIGN KEY (manifest) REFERENCES public.em_manifests(uuid) ON DELETE RESTRICT",
    "ALTER TABLE ONLY public.em_elements\n    ADD CONSTRAINT em_elements_profile_fkey FOREIGN KEY (profile) REFERENCES public.vs_profiles(uuid) ON DELETE RESTRICT",
    "ALTER TABLE ONLY public.em_exports\n    ADD CONSTRAINT em_exports_element_fkey FOREIGN KEY (element) REFERENCES public.em_elements(uuid) ON DELETE CASCADE",
    "ALTER TABLE ONLY public.em_imports\n    ADD CONSTRAINT em_imports_element_fkey FOREIGN KEY (element) REFERENCES public.em_elements(uuid) ON DELETE CASCADE",
    "ALTER TABLE ONLY public.em_imports\n    ADD CONSTRAINT em_imports_from_element_fkey FOREIGN KEY (from_element) REFERENCES public.em_elements(uuid) ON DELETE CASCADE",
    "ALTER TABLE ONLY public.em_imports\n    ADD CONSTRAINT em_imports_from_resource_fkey FOREIGN KEY (from_resource) REFERENCES public.em_resources(uuid) ON DELETE CASCADE",
    "ALTER TABLE ONLY public.em_resources\n    ADD CONSTRAINT em_resources_actual_resource_fkey FOREIGN KEY (actual_resource) REFERENCES public.ua_actual_resources(res_uuid) ON DELETE SET NULL",
    "ALTER TABLE ONLY public.em_resources\n    ADD CONSTRAINT em_resources_element_fkey FOREIGN KEY (element) REFERENCES public.em_elements(uuid) ON DELETE CASCADE",
    "ALTER TABLE ONLY public.em_resources\n    ADD CONSTRAINT em_resources_target_resource_fkey FOREIGN KEY (target_resource) REFERENCES public.ua_target_resources(res_uuid)",
    "ALTER TABLE ONLY public.iam_binding_permissions\n    ADD CONSTRAINT iam_binding_permissions_permission_fkey FOREIGN KEY (permission) REFERENCES public.iam_permissions(uuid)",
    "ALTER TABLE ONLY public.iam_binding_permissions\n    ADD CONSTRAINT iam_binding_permissions_role_fkey FOREIGN KEY (role) REFERENCES public.iam_roles(uuid)",
    "ALTER TABLE ONLY public.iam_binding_roles\n    ADD CONSTRAINT iam_binding_roles_project_fkey FOREIGN KEY (project) REFERENCES public.iam_projects(uuid)",
    "ALTER TABLE ONLY public.iam_binding_roles\n    ADD CONSTRAINT iam_binding_roles_role_fkey FOREIGN KEY (role) REFERENCES public.iam_roles(uuid)",
    'ALTER TABLE ONLY public.iam_binding_roles\n    ADD CONSTRAINT iam_binding_roles_user_fkey FOREIGN KEY ("user") REFERENCES public.iam_users(uuid)',
    "ALTER TABLE ONLY public.iam_idp_authorization_info\n    ADD CONSTRAINT iam_idp_authorization_info_idp_fkey FOREIGN KEY (idp) REFERENCES public.iam_idp(uuid)",
    "ALTER TABLE ONLY public.iam_idp_authorization_info\n    ADD CONSTRAINT iam_idp_authorization_info_token_fkey FOREIGN KEY (token) REFERENCES public.iam_tokens(uuid)",
    "ALTER TABLE ONLY public.iam_idp\n    ADD CONSTRAINT iam_idp_iam_client_fkey FOREIGN KEY (iam_client) REFERENCES public.iam_clients(uuid) ON UPDATE RESTRICT ON DELETE RESTRICT",
    "ALTER TABLE ONLY public.iam_organization_members\n    ADD CONSTRAINT iam_organization_members_organization_fkey FOREIGN KEY (organization) REFERENCES public.iam_organizations(uuid) ON UPDATE CASCADE ON DELETE CASCADE",
    'ALTER TABLE ONLY public.iam_organization_members\n    ADD CONSTRAINT iam_organization_members_user_fkey FOREIGN KEY ("user") REFERENCES public.iam_users(uuid) ON UPDATE CASCADE ON DELETE CASCADE',
    "ALTER TABLE ONLY public.iam_projects\n    ADD CONSTRAINT iam_projects_organization_fkey FOREIGN KEY (organization) REFERENCES public.iam_organizations(uuid)",
    "ALTER TABLE ONLY public.iam_tokens\n    ADD CONSTRAINT iam_tokens_iam_client_fkey FOREIGN KEY (iam_client) REFERENCES public.iam_clients(uuid) ON UPDATE CASCADE ON DELETE CASCADE",
    "ALTER TABLE ONLY public.iam_tokens\n    ADD CONSTRAINT iam_tokens_project_fkey FOREIGN KEY (project) REFERENCES public.iam_projects(uuid) ON UPDATE CASCADE ON DELETE CASCADE",
    'ALTER TABLE ONLY public.iam_tokens\n    ADD CONSTRAINT iam_tokens_user_fkey FOREIGN KEY ("user") REFERENCES public.iam_users(uuid) ON UPDATE CASCADE ON DELETE CASCADE',
    "ALTER TABLE ONLY public.iam_users\n    ADD CONSTRAINT iam_users_registration_client_fkey FOREIGN KEY (registration_client) REFERENCES public.iam_clients(uuid) ON DELETE SET NULL",
    "ALTER TABLE ONLY public.machines\n    ADD CONSTRAINT machines_node_fkey FOREIGN KEY (node) REFERENCES public.nodes(uuid) ON DELETE CASCADE",
    "ALTER TABLE ONLY public.machines\n    ADD CONSTRAINT machines_pool_fkey FOREIGN KEY (pool) REFERENCES public.machine_pools(uuid) ON DELETE SET NULL",
    "ALTER TABLE ONLY public.n_machine_pool_reservations\n    ADD CONSTRAINT n_machine_pool_reservations_machine_fkey FOREIGN KEY (machine) REFERENCES public.machines(uuid) ON DELETE CASCADE",
    "ALTER TABLE ONLY public.n_machine_pool_reservations\n    ADD CONSTRAINT n_machine_pool_reservations_pool_fkey FOREIGN KEY (pool) REFERENCES public.machine_pools(uuid) ON DELETE CASCADE",
    "ALTER TABLE ONLY public.net_lb_backendpools\n    ADD CONSTRAINT net_lb_backendpools_parent_fkey FOREIGN KEY (parent) REFERENCES public.net_lb(uuid)",
    "ALTER TABLE ONLY public.net_lb_vhosts\n    ADD CONSTRAINT net_lb_vhosts_parent_fkey FOREIGN KEY (parent) REFERENCES public.net_lb(uuid)",
    "ALTER TABLE ONLY public.net_lb_vhosts_routes\n    ADD CONSTRAINT net_lb_vhosts_routes_parent_fkey FOREIGN KEY (parent) REFERENCES public.net_lb_vhosts(uuid)",
    "ALTER TABLE ONLY public.node_volumes\n    ADD CONSTRAINT node_volumes_node_fkey FOREIGN KEY (node) REFERENCES public.nodes(uuid) ON DELETE CASCADE",
    "ALTER TABLE ONLY public.node_volumes\n    ADD CONSTRAINT node_volumes_pool_fkey FOREIGN KEY (pool) REFERENCES public.machine_pools(uuid) ON DELETE SET NULL",
    "ALTER TABLE ONLY public.nodes\n    ADD CONSTRAINT nodes_node_set_fkey FOREIGN KEY (node_set) REFERENCES public.compute_sets(uuid) ON DELETE CASCADE",
    "ALTER TABLE ONLY public.repo_artifacts\n    ADD CONSTRAINT repo_artifacts_element_fkey FOREIGN KEY (element) REFERENCES public.repo_elements(uuid) ON DELETE CASCADE",
    "ALTER TABLE ONLY public.repo_element_deps_bindings\n    ADD CONSTRAINT repo_element_deps_bindings_depends_on_fkey FOREIGN KEY (depends_on) REFERENCES public.repo_elements(uuid) ON DELETE CASCADE",
    "ALTER TABLE ONLY public.repo_element_deps_bindings\n    ADD CONSTRAINT repo_element_deps_bindings_element_fkey FOREIGN KEY (element) REFERENCES public.repo_elements(uuid) ON DELETE CASCADE",
    "ALTER TABLE ONLY public.repo_elements\n    ADD CONSTRAINT repo_elements_element_fkey FOREIGN KEY (element) REFERENCES public.em_elements(uuid) ON DELETE SET NULL",
    "ALTER TABLE ONLY public.repo_elements\n    ADD CONSTRAINT repo_elements_repository_fkey FOREIGN KEY (repository) REFERENCES public.repo_repositories(uuid) ON DELETE RESTRICT",
    "ALTER TABLE ONLY public.vs_values\n    ADD CONSTRAINT vs_values_variable_fkey FOREIGN KEY (variable) REFERENCES public.vs_variables(uuid) ON DELETE SET NULL",
    "CREATE OR REPLACE VIEW public.compute_hw_nodes_without_ports AS\n SELECT nodes.uuid,\n    nodes.uuid AS node,\n    machines.uuid AS machine,\n    compute_net_interfaces.uuid AS iface\n   FROM (((public.nodes\n     LEFT JOIN public.machines ON ((nodes.uuid = machines.node)))\n     LEFT JOIN public.compute_net_interfaces ON ((compute_net_interfaces.machine = machines.uuid)))\n     LEFT JOIN public.compute_ports ON ((compute_ports.node = nodes.uuid)))\n  WHERE (((nodes.node_type)::text = 'HW'::text) AND (machines.uuid IS NOT NULL) AND (compute_net_interfaces.ipv4 IS NOT NULL) AND (compute_ports.uuid IS NULL))",
    "CREATE OR REPLACE VIEW public.compute_nodes_without_ports AS\n SELECT nodes.uuid,\n    nodes.project_id,\n    nodes.name,\n    nodes.description,\n    nodes.cores,\n    nodes.ram,\n    nodes.node_type,\n    nodes.status,\n    nodes.created_at,\n    nodes.updated_at,\n    nodes.default_network,\n    nodes.node_set,\n    nodes.placement_policies,\n    nodes.disk_spec,\n    nodes.hostname\n   FROM (public.nodes\n     LEFT JOIN public.compute_ports ports ON ((nodes.uuid = ports.node)))\n  WHERE (ports.uuid IS NULL)",
    "CREATE OR REPLACE VIEW public.compute_unscheduled_volumes AS\n SELECT node_volumes.uuid,\n    node_volumes.uuid AS volume\n   FROM (public.node_volumes\n     LEFT JOIN public.compute_machine_volumes ON ((node_volumes.uuid = compute_machine_volumes.node_volume)))\n  WHERE (compute_machine_volumes.uuid IS NULL)",
    "CREATE OR REPLACE VIEW public.domainmetadata AS\n SELECT id,\n    domain_id,\n    kind,\n    content\n   FROM public.dns_domainmetadata",
    "CREATE OR REPLACE VIEW public.domains AS\n SELECT id,\n    name,\n    master,\n    last_check,\n    type,\n    notified_serial,\n    account,\n    options,\n    catalog\n   FROM public.dns_domains",
    """CREATE OR REPLACE VIEW public.em_incorrect_resource_statuses_view AS
SELECT
    er.uuid,
    er.status AS current_status,
    (
        CASE
            WHEN utr.hash IS NULL THEN uar.status
            WHEN uar.status = 'ACTIVE' AND utr.hash = uar.hash THEN 'ACTIVE'
            WHEN uar.status IS NULL THEN NULL
            ELSE 'IN_PROGRESS'
        END
    )::varchar(32) AS actual_status
FROM public.em_resources er
LEFT JOIN (
    SELECT uuid, hash
    FROM public.ua_target_resources
    WHERE kind LIKE 'em_%'
) utr ON er.uuid = utr.uuid
LEFT JOIN (
    SELECT uuid, status, hash
    FROM public.ua_actual_resources
    WHERE kind LIKE 'em_%'
) uar ON er.uuid = uar.uuid
WHERE er.status IS DISTINCT FROM (
    CASE
        WHEN utr.hash IS NULL THEN uar.status
        WHEN uar.status = 'ACTIVE' AND utr.hash = uar.hash THEN 'ACTIVE'
        WHEN uar.status IS NULL THEN NULL
        ELSE 'IN_PROGRESS'
    END
)::varchar(32)""",
    "CREATE OR REPLACE VIEW public.em_incorrect_statuses_view AS\n WITH em_incorrect_resource_statuses AS (\n         WITH tmp AS (\n                 SELECT em_resources.element,\n                    bool_or(((em_resources.status)::text = 'IN_PROGRESS'::text)) AS has_in_progress,\n                    bool_and(((em_resources.status)::text = 'ACTIVE'::text)) AS all_active,\n                    bool_and(((em_resources.status)::text = 'NEW'::text)) AS all_new,\n                    count(*) AS resources_count\n                   FROM public.em_resources\n                  GROUP BY em_resources.element\n                )\n         SELECT e.uuid,\n            e.name,\n            e.status AS api_status,\n                CASE\n                    WHEN (tmp.resources_count IS NULL) THEN 'ACTIVE'::text\n                    WHEN tmp.has_in_progress THEN 'IN_PROGRESS'::text\n                    WHEN tmp.all_active THEN 'ACTIVE'::text\n                    WHEN tmp.all_new THEN 'NEW'::text\n                    ELSE 'IN_PROGRESS'::text\n                END AS actual_status\n           FROM (public.em_elements e\n             LEFT JOIN tmp tmp ON ((e.uuid = tmp.element)))\n        )\n SELECT uuid,\n    name,\n    api_status,\n    actual_status\n   FROM em_incorrect_resource_statuses eis\n  WHERE ((api_status)::text <> actual_status)",
    "CREATE OR REPLACE VIEW public.em_outdated_resources_view AS\n SELECT COALESCE(er.uuid, utr.uuid) AS uuid,\n    er.uuid AS em_resource,\n    utr.res_uuid AS target_resource\n   FROM (public.em_resources er\n     FULL JOIN ( SELECT ua_target_resources.uuid,\n            ua_target_resources.res_uuid,\n            ua_target_resources.updated_at,\n            ua_target_resources.tracked_at\n           FROM public.ua_target_resources\n          WHERE ((ua_target_resources.kind)::text ~~ 'em_%'::text)) utr ON ((er.uuid = utr.uuid)))\n  WHERE ((er.uuid IS NULL) OR (utr.uuid IS NULL) OR (er.updated_at <> utr.tracked_at))",
    'CREATE OR REPLACE VIEW public.iam_permissions_fast_view AS\n SELECT t1.uuid,\n    t1.uuid AS permission,\n    t4.uuid AS "user",\n    t3.uuid AS role,\n    t3.project\n   FROM (((public.iam_permissions t1\n     LEFT JOIN public.iam_binding_permissions t2 ON ((t2.permission = t1.uuid)))\n     LEFT JOIN public.iam_binding_roles t3 ON ((t3.role = t2.role)))\n     LEFT JOIN public.iam_users t4 ON ((t4.uuid = t3."user")))',
    "CREATE OR REPLACE VIEW public.netboots AS\n SELECT firmware_uuid AS uuid,\n    boot\n   FROM public.machines",
    "CREATE OR REPLACE VIEW public.records AS\n SELECT domain_id,\n    name,\n    type,\n    content,\n    ttl,\n    prio,\n    disabled,\n    ordername,\n    auth\n   FROM public.dns_records",
    "CREATE OR REPLACE VIEW public.unscheduled_nodes AS\n SELECT nodes.uuid,\n    nodes.uuid AS node\n   FROM (public.nodes\n     LEFT JOIN public.machines ON ((nodes.uuid = machines.node)))\n  WHERE (machines.uuid IS NULL)",
)
VIEWS = (
    "compute_hw_nodes_without_ports",
    "compute_nodes_without_ports",
    "compute_unscheduled_volumes",
    "domainmetadata",
    "domains",
    "em_incorrect_resource_statuses_view",
    "em_incorrect_statuses_view",
    "em_outdated_resources_view",
    "iam_permissions_fast_view",
    "netboots",
    "records",
    "unscheduled_nodes",
)
TABLES = (
    "compute_net_interfaces",
    "compute_ports",
    "machines",
    "nodes",
    "compute_machine_volumes",
    "compute_networks",
    "compute_placement_domains",
    "compute_placement_policies",
    "compute_placement_policy_allocations",
    "compute_placement_zones",
    "compute_sets",
    "compute_subnets",
    "node_volumes",
    "config_configs",
    "dns_domainmetadata",
    "dns_domains",
    "dns_records",
    "em_elements",
    "em_exports",
    "em_imports",
    "em_resources",
    "em_manifests",
    "em_services",
    "gcl_sdk_audit_logs",
    "gcl_sdk_events",
    "iam_binding_permissions",
    "iam_binding_roles",
    "iam_clients",
    "iam_idp",
    "iam_idp_authorization_info",
    "iam_organization_members",
    "iam_organizations",
    "iam_permissions",
    "iam_users",
    "iam_projects",
    "iam_roles",
    "iam_tokens",
    "machine_pools",
    "n_builders",
    "n_machine_pool_reservations",
    "net_border",
    "net_lb",
    "net_lb_backendpools",
    "net_lb_vhosts",
    "net_lb_vhosts_routes",
    "repo_artifacts",
    "repo_element_deps_bindings",
    "repo_elements",
    "repo_repositories",
    "secret_certificates",
    "secret_passwords",
    "secret_rsa_keys",
    "secret_ssh_keys",
    "security_rules",
    "storage_certs",
    "storage_passwords",
    "vs_profiles",
    "vs_values",
    "vs_variables",
)
SEQUENCES = (
    "dns_domain_id_seq",
    "dns_domainmetadata_id_seq",
)
TYPES = (
    "enum_agent_status",
    "enum_config_status",
    "enum_secret_status",
    "enum_service_status",
    "enum_service_target_status",
    "user_type_enum",
)


INDEX_STATEMENTS = (
    "CREATE UNIQUE INDEX IF NOT EXISTS compute_net_interfaces_mac_machine_id_idx ON public.compute_net_interfaces USING btree (mac, machine)",
    "CREATE INDEX IF NOT EXISTS compute_net_interfaces_machine_id_idx ON public.compute_net_interfaces USING btree (machine)",
    "CREATE INDEX IF NOT EXISTS compute_networks_project_id_idx ON public.compute_networks USING btree (project_id)",
    "CREATE INDEX IF NOT EXISTS compute_placement_policies_domain_idx ON public.compute_placement_policies USING btree (domain)",
    "CREATE INDEX IF NOT EXISTS compute_placement_policies_project_id_idx ON public.compute_placement_policies USING btree (project_id)",
    "CREATE INDEX IF NOT EXISTS compute_placement_policies_zone_idx ON public.compute_placement_policies USING btree (zone)",
    "CREATE INDEX IF NOT EXISTS compute_placement_policy_allocations_node_idx ON public.compute_placement_policy_allocations USING btree (node)",
    "CREATE INDEX IF NOT EXISTS compute_placement_policy_allocations_policy_idx ON public.compute_placement_policy_allocations USING btree (policy)",
    "CREATE INDEX IF NOT EXISTS compute_placement_zones_domain_idx ON public.compute_placement_zones USING btree (domain)",
    "CREATE UNIQUE INDEX IF NOT EXISTS compute_ports_mac_subnet_id_idx ON public.compute_ports USING btree (mac, subnet)",
    "CREATE INDEX IF NOT EXISTS compute_ports_node_id_idx ON public.compute_ports USING btree (node)",
    "CREATE INDEX IF NOT EXISTS compute_ports_project_id_idx ON public.compute_ports USING btree (project_id)",
    "CREATE INDEX IF NOT EXISTS compute_ports_subnet_id_idx ON public.compute_ports USING btree (subnet)",
    "CREATE UNIQUE INDEX IF NOT EXISTS compute_ports_target_ipv4_subnet_id_idx ON public.compute_ports USING btree (target_ipv4, subnet)",
    "CREATE INDEX IF NOT EXISTS compute_sets_project_id_idx ON public.compute_sets USING btree (project_id)",
    "CREATE INDEX IF NOT EXISTS compute_subnets_network_id_idx ON public.compute_subnets USING btree (network)",
    "CREATE INDEX IF NOT EXISTS compute_subnets_project_id_idx ON public.compute_subnets USING btree (project_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS config_configs_path_target_id_idx ON public.config_configs USING btree (path, target)",
    "CREATE INDEX IF NOT EXISTS config_configs_project_id_idx ON public.config_configs USING btree (project_id)",
    "CREATE INDEX IF NOT EXISTS dns_domainmetadata_domain_id_idx ON public.dns_domainmetadata USING btree (domain_id)",
    "CREATE INDEX IF NOT EXISTS dns_domains_catalog_idx ON public.dns_domains USING btree (catalog)",
    "CREATE UNIQUE INDEX IF NOT EXISTS dns_domains_id_idx ON public.dns_domains USING btree (id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS dns_domains_name_idx ON public.dns_domains USING btree (name)",
    "CREATE INDEX IF NOT EXISTS dns_domains_project_id_name_idx ON public.dns_domains USING btree (project_id, name)",
    "CREATE INDEX IF NOT EXISTS dns_records_project_id_idx ON public.dns_records USING btree (project_id)",
    "CREATE INDEX IF NOT EXISTS dns_records_updated_at_idx ON public.dns_records USING btree (updated_at)",
    "CREATE INDEX IF NOT EXISTS domain_id ON public.dns_records USING btree (domain_id)",
    "CREATE INDEX IF NOT EXISTS em_exports_element_idx ON public.em_exports USING btree (element)",
    "CREATE INDEX IF NOT EXISTS em_imports_element_idx ON public.em_imports USING btree (element)",
    "CREATE INDEX IF NOT EXISTS em_resources_element_idx ON public.em_resources USING btree (element)",
    "CREATE UNIQUE INDEX IF NOT EXISTS em_services_path_target_id_idx ON public.em_services USING btree (path, target)",
    "CREATE INDEX IF NOT EXISTS em_services_project_id_idx ON public.em_services USING btree (project_id)",
    "CREATE INDEX IF NOT EXISTS gcl_sdk_audit_logs_object_type_action_idx ON public.gcl_sdk_audit_logs USING btree (object_type, action)",
    "CREATE INDEX IF NOT EXISTS iam_binding_permissions_role_permission_idx ON public.iam_binding_permissions USING btree (role, permission)",
    "CREATE INDEX IF NOT EXISTS iam_binding_roles_project_idx ON public.iam_binding_roles USING btree (project)",
    "CREATE INDEX IF NOT EXISTS iam_binding_roles_role_idx ON public.iam_binding_roles USING btree (role)",
    'CREATE INDEX IF NOT EXISTS iam_binding_roles_user_idx ON public.iam_binding_roles USING btree ("user")',
    "CREATE UNIQUE INDEX IF NOT EXISTS iam_client_id_idx ON public.iam_clients USING btree (client_id)",
    "CREATE INDEX IF NOT EXISTS iam_organizations_name_idx ON public.iam_organizations USING btree (name)",
    "CREATE UNIQUE INDEX IF NOT EXISTS iam_permissions_name_idx ON public.iam_permissions USING btree (name)",
    "CREATE INDEX IF NOT EXISTS iam_projects_name_idx ON public.iam_projects USING btree (name)",
    "CREATE INDEX IF NOT EXISTS iam_projects_organization_idx ON public.iam_projects USING btree (organization)",
    "CREATE INDEX IF NOT EXISTS iam_roles_name_idx ON public.iam_roles USING btree (name)",
    "CREATE INDEX IF NOT EXISTS iam_tokens_iam_client_idx ON public.iam_tokens USING btree (iam_client)",
    "CREATE UNIQUE INDEX IF NOT EXISTS iam_users_email_lower_idx ON public.iam_users USING btree (lower((email)::text))",
    "CREATE UNIQUE INDEX IF NOT EXISTS iam_users_name_lower_idx ON public.iam_users USING btree (lower((name)::text))",
    "CREATE INDEX IF NOT EXISTS idx_compute_machine_volumes_machine ON public.compute_machine_volumes USING btree (machine)",
    "CREATE INDEX IF NOT EXISTS idx_compute_machine_volumes_node_volume ON public.compute_machine_volumes USING btree (node_volume)",
    "CREATE INDEX IF NOT EXISTS idx_compute_machine_volumes_pool ON public.compute_machine_volumes USING btree (pool)",
    "CREATE INDEX IF NOT EXISTS idx_compute_machine_volumes_project_id ON public.compute_machine_volumes USING btree (project_id)",
    "CREATE INDEX IF NOT EXISTS nametype_index ON public.dns_records USING btree (name, type)",
    "CREATE INDEX IF NOT EXISTS net_border_project_id_name_idx ON public.net_border USING btree (project_id, name)",
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
    "CREATE INDEX IF NOT EXISTS repo_artifacts_element_idx ON public.repo_artifacts USING btree (element)",
    "CREATE UNIQUE INDEX IF NOT EXISTS repo_artifacts_element_urn_idx ON public.repo_artifacts USING btree (element, urn)",
    "CREATE INDEX IF NOT EXISTS repo_artifacts_project_id_idx ON public.repo_artifacts USING btree (project_id)",
    "CREATE INDEX IF NOT EXISTS repo_element_deps_bindings_depends_on_idx ON public.repo_element_deps_bindings USING btree (depends_on)",
    "CREATE UNIQUE INDEX IF NOT EXISTS repo_element_deps_bindings_element_depends_on_idx ON public.repo_element_deps_bindings USING btree (element, depends_on)",
    "CREATE INDEX IF NOT EXISTS repo_element_deps_bindings_element_idx ON public.repo_element_deps_bindings USING btree (element)",
    "CREATE INDEX IF NOT EXISTS repo_elements_installation_state_idx ON public.repo_elements USING btree (installation_state)",
    "CREATE INDEX IF NOT EXISTS repo_elements_name_idx ON public.repo_elements USING btree (name)",
    "CREATE INDEX IF NOT EXISTS repo_elements_name_installation_state_idx ON public.repo_elements USING btree (name, installation_state)",
    "CREATE INDEX IF NOT EXISTS repo_elements_project_id_idx ON public.repo_elements USING btree (project_id)",
    "CREATE INDEX IF NOT EXISTS repo_elements_repository_idx ON public.repo_elements USING btree (repository)",
    "CREATE UNIQUE INDEX IF NOT EXISTS repo_elements_repository_name_version_idx ON public.repo_elements USING btree (repository, name, version)",
    "CREATE INDEX IF NOT EXISTS repo_elements_status_idx ON public.repo_elements USING btree (status)",
    "CREATE UNIQUE INDEX IF NOT EXISTS repo_repositories_driver_spec_idx ON public.repo_repositories USING btree (driver_spec)",
    "CREATE INDEX IF NOT EXISTS repo_repositories_project_id_idx ON public.repo_repositories USING btree (project_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS repo_repositories_project_id_name_idx ON public.repo_repositories USING btree (project_id, name)",
    "CREATE INDEX IF NOT EXISTS repo_repositories_status_idx ON public.repo_repositories USING btree (status)",
    "CREATE INDEX IF NOT EXISTS secret_certificates_project_id_idx ON public.secret_certificates USING btree (project_id)",
    "CREATE INDEX IF NOT EXISTS secret_passwords_project_id_idx ON public.secret_passwords USING btree (project_id)",
    "CREATE INDEX IF NOT EXISTS secret_rsa_keys_project_id_idx ON public.secret_rsa_keys USING btree (project_id)",
    "CREATE INDEX IF NOT EXISTS secret_ssh_keys_project_id_idx ON public.secret_ssh_keys USING btree (project_id)",
    "CREATE INDEX IF NOT EXISTS security_rules_name_idx ON public.security_rules USING btree (name)",
    "CREATE INDEX IF NOT EXISTS security_rules_project_id_idx ON public.security_rules USING btree (project_id)",
    "CREATE INDEX IF NOT EXISTS vs_profiles_profile_type_idx ON public.vs_profiles USING btree (profile_type)",
    "CREATE INDEX IF NOT EXISTS vs_profiles_project_id_idx ON public.vs_profiles USING btree (project_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS vs_values_one_manual_selected_per_variable ON public.vs_values USING btree (variable) WHERE (manual_selected = true)",
    "CREATE INDEX IF NOT EXISTS vs_values_project_id_idx ON public.vs_values USING btree (project_id)",
    "CREATE INDEX IF NOT EXISTS vs_variables_project_id_idx ON public.vs_variables USING btree (project_id)",
)


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0000-root-d34de1.py"]

    @property
    def migration_id(self):
        return "7f2e4a90-3c6d-45ed-9da3-e1f8b2a7c619"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        for statement in SCHEMA_STATEMENTS + INDEX_STATEMENTS:
            if statement.startswith("CREATE TYPE public."):
                type_name = statement.split()[2].removeprefix("public.")
                existing_type = session.execute(
                    f"SELECT 1 FROM pg_catalog.pg_type WHERE typname = '{type_name}'",
                    None,
                )
                if existing_type.rowcount:
                    continue
            if statement.startswith("CREATE TABLE public."):
                table_name = statement.split()[2].removeprefix("public.")
                existing_table = session.execute(
                    f"SELECT to_regclass('public.{table_name}')", None
                )
                if existing_table.fetchone()["to_regclass"] is not None:
                    continue
            if "ADD CONSTRAINT" in statement:
                table_name = statement.split()[3].removeprefix("public.")
                constraint_name = statement.split("ADD CONSTRAINT ", 1)[1].split()[0]
                existing_constraint = session.execute(
                    "SELECT 1 FROM pg_catalog.pg_constraint AS pg_constraint "
                    "JOIN pg_catalog.pg_class AS pg_class "
                    "ON pg_class.oid = pg_constraint.conrelid "
                    f"WHERE pg_constraint.conname = '{constraint_name}' "
                    f"AND pg_class.relname = '{table_name}'",
                    None,
                )
                if existing_constraint.rowcount:
                    continue
            session.execute(statement, None)
        self._upgrade_data(session)

    def downgrade(self, session):
        for view in VIEWS:
            session.execute(f"DROP VIEW IF EXISTS public.{view} CASCADE", None)
        for table in TABLES:
            session.execute(f"DROP TABLE IF EXISTS public.{table} CASCADE", None)
        for sequence in SEQUENCES:
            session.execute(f"DROP SEQUENCE IF EXISTS public.{sequence} CASCADE", None)
        for type_name in TYPES:
            if type_name != "enum_agent_status":
                session.execute(f"DROP TYPE IF EXISTS public.{type_name} CASCADE", None)

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
        self._upgrade_network_lb(session)
        self._upgrade_service_token(session)
        self._upgrade_iam_user_create(session)
        self._upgrade_border_permissions(session)

    def _downgrade_data(self, session):
        self._downgrade_border_permissions(session)
        self._downgrade_iam_user_create(session)
        self._downgrade_service_token(session)
        self._downgrade_network_lb(session)
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

    def _upgrade_border_permissions(self, session):
        permissions = (
            ("network.border.read", "List and read borders (NAT gateways)"),
            ("network.border.create", "Create borders (NAT gateways)"),
            ("network.border.update", "Update borders (NAT gateways)"),
            ("network.border.delete", "Delete borders (NAT gateways)"),
        )
        for name, description in permissions:
            session.execute(
                f"""
                INSERT INTO iam_permissions (uuid, name, description)
                VALUES ('{_u(name)}', '{name}', '{description}')
                ON CONFLICT (uuid) DO NOTHING;
            """,
                None,
            )
            session.execute(
                f"""
                INSERT INTO iam_binding_permissions (uuid, role, permission, project_id)
                VALUES ('{_u("binding." + name)}', '{OWNER_ROLE_UUID}',
                        '{_u(name)}', '{COMPUTE_PROJECT_UUID}')
                ON CONFLICT (uuid) DO NOTHING;
            """,
                None,
            )

    def _downgrade_border_permissions(self, session):
        for name in (
            "network.border.read",
            "network.border.create",
            "network.border.update",
            "network.border.delete",
        ):
            session.execute(
                f"DELETE FROM iam_binding_permissions WHERE permission = '{_u(name)}'",
                None,
            )
            session.execute(
                f"DELETE FROM iam_permissions WHERE uuid = '{_u(name)}'", None
            )


migration_step = MigrationStep()
