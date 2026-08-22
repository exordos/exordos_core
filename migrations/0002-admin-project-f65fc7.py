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

import uuid as sys_uuid

from restalchemy.storage.sql import migrations

from exordos_core.common.constants import ADMIN_PROJECT_UUID
from exordos_core.common.constants import ZERO_UUID

ZERO_UUID = str(ZERO_UUID)
ADMIN_PROJECT_UUID = str(ADMIN_PROJECT_UUID)

# Tables owning resources via the project_id column
PROJECT_ID_TABLES = (
    "compute_machine_volumes",
    "compute_networks",
    "compute_placement_policies",
    "compute_ports",
    "compute_sets",
    "compute_subnets",
    "config_configs",
    "dns_domains",
    "dns_records",
    "em_elements",
    "em_manifests",
    "em_services",
    "iam_binding_permissions",
    "iam_clients",
    "iam_idp",
    "iam_roles",
    "machines",
    "net_border",
    "net_lb",
    "net_lb_backendpools",
    "net_lb_vhosts",
    "net_lb_vhosts_routes",
    "node_volumes",
    "nodes",
    "quota_limits",
    "repo_artifacts",
    "repo_elements",
    "repo_repositories",
    "secret_certificates",
    "secret_passwords",
    "secret_rsa_keys",
    "secret_ssh_keys",
    "security_rules",
    "vs_profiles",
    "vs_values",
    "vs_variables",
)

# Tables referencing a project via the project column
PROJECT_TABLES = (
    "iam_binding_roles",
    "iam_tokens",
)

# The admin project is declared in the core element manifest, so the element
# manager and the universal agent keep their own copies of it addressed by
# the project UUID.
ADMIN_PROJECT_LINK_PREFIX = "$core.iam.projects"
ADMIN_PROJECT_RESOURCE_NAME = "admin"
ADMIN_PROJECT_RESOURCE_KIND = "em_core_iam_projects"


def em_resource_uuid(project_uuid):
    """Return the universal agent resource UUID of the admin project."""
    return str(sys_uuid.uuid5(sys_uuid.UUID(project_uuid), ADMIN_PROJECT_RESOURCE_KIND))


def move_admin_project_payloads(session, old_uuid, new_uuid):
    """Move the references kept inside JSON payloads and scope strings."""
    # A token keeps its project as a "project:<uuid>" string and resolves
    # the project from it on every refresh.
    session.execute(
        f"""
        UPDATE iam_tokens
        SET scope = replace(scope, 'project:{old_uuid}', 'project:{new_uuid}')
        WHERE scope LIKE '%project:{old_uuid}%';
        """
    )
    # The secret agent stores its own copy of a password resource.
    session.execute(
        f"""
        UPDATE storage_passwords
        SET meta = jsonb_set(meta, '{{project_id}}', '"{new_uuid}"')
        WHERE meta->>'project_id' = '{old_uuid}';
        """
    )
    # The manifest that declares the admin project.
    manifest_path = (
        f'{{"{ADMIN_PROJECT_LINK_PREFIX}",{ADMIN_PROJECT_RESOURCE_NAME},uuid}}'
    )
    session.execute(
        f"""
        UPDATE em_manifests
        SET resources = jsonb_set(
            resources, '{manifest_path}', '"{new_uuid}"'
        )
        WHERE resources #>> '{manifest_path}' = '{old_uuid}';
        """
    )
    # The element manager resource rendered from that manifest and the
    # universal agent resources it is linked to. The links are dropped first
    # because they are foreign keys on the UUID being replaced.
    old_res_uuid = em_resource_uuid(old_uuid)
    new_res_uuid = em_resource_uuid(new_uuid)
    session.execute(
        f"""
        UPDATE em_resources
        SET target_resource = NULL,
            actual_resource = NULL
        WHERE uuid = '{old_uuid}'
          AND resource_link_prefix = '{ADMIN_PROJECT_LINK_PREFIX}';
        """
    )
    for table in ("ua_target_resources", "ua_actual_resources"):
        session.execute(
            f"""
            UPDATE {table}
            SET res_uuid = '{new_res_uuid}',
                uuid = '{new_uuid}',
                value = jsonb_set(value, '{{uuid}}', '"{new_uuid}"')
            WHERE res_uuid = '{old_res_uuid}'
              AND kind = '{ADMIN_PROJECT_RESOURCE_KIND}';
            """
        )
    session.execute(
        f"""
        UPDATE em_resources
        SET uuid = '{new_uuid}',
            value = jsonb_set(value, '{{uuid}}', '"{new_uuid}"'),
            target_resource = (
                SELECT res_uuid
                FROM ua_target_resources
                WHERE res_uuid = '{new_res_uuid}'
            ),
            actual_resource = (
                SELECT res_uuid
                FROM ua_actual_resources
                WHERE res_uuid = '{new_res_uuid}'
            )
        WHERE uuid = '{old_uuid}'
          AND resource_link_prefix = '{ADMIN_PROJECT_LINK_PREFIX}';
        """
    )
    # Every other target resource embeds project_id in a value that is
    # hashed with xxhash, so the payload cannot be patched from SQL without
    # invalidating the hashes. Reset the tracking timestamp instead: the
    # owning builder then rebuilds the value and both hashes from the
    # instance, whose project_id is already moved.
    session.execute(
        f"""
        UPDATE ua_target_resources
        SET tracked_at = 'epoch'
        WHERE value->>'project_id' = '{old_uuid}';
        """
    )


def move_admin_project(session, old_uuid, new_uuid):
    session.execute(
        f"""
        INSERT INTO iam_projects (
            uuid, status, name, description, organization, created_at, updated_at
        )
        SELECT
            '{new_uuid}',
            status,
            name,
            description,
            organization,
            created_at,
            updated_at
        FROM iam_projects
        WHERE uuid = '{old_uuid}'
        ON CONFLICT (uuid) DO NOTHING;
        """
    )
    for table in PROJECT_ID_TABLES:
        session.execute(
            f"""
            UPDATE {table}
            SET project_id = '{new_uuid}'
            WHERE project_id = '{old_uuid}';
            """
        )
    for table in PROJECT_TABLES:
        session.execute(
            f"""
            UPDATE {table}
            SET project = '{new_uuid}'
            WHERE project = '{old_uuid}';
            """
        )
    move_admin_project_payloads(session, old_uuid, new_uuid)
    session.execute(
        f"""
        DELETE FROM iam_projects
        WHERE uuid = '{old_uuid}';
        """
    )


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0001-zero-entities-5a7a0e.py"]

    @property
    def migration_id(self):
        return "f65fc7df-bc5f-4fa6-9763-cce0fefedb37"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        move_admin_project(session, ZERO_UUID, ADMIN_PROJECT_UUID)

    def downgrade(self, session):
        move_admin_project(session, ADMIN_PROJECT_UUID, ZERO_UUID)


migration_step = MigrationStep()
