# Copyright 2016 Eugene Frolov <eugene@frolov.net.ru>
#
# All Rights Reserved.
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

from restalchemy.storage.sql import migrations

from exordos_core.common.constants import ADMIN_ORGANIZATION_MEMBER_UUID
from exordos_core.common.constants import EXORDOS_CORE_ORGANIZATION_UUID
from exordos_core.common.constants import GENESIS_ORGANIZATION_MEMBER_UUID
from exordos_core.common.constants import NEW_ADMIN_ORG_UUID
from exordos_core.common.constants import NEW_ADMIN_ROLE_BINDING_UUID
from exordos_core.common.constants import NEW_ADMIN_ROLE_UUID
from exordos_core.common.constants import NEW_ALLOW_ALL_BINDING_UUID
from exordos_core.common.constants import NEW_ALLOW_ALL_PERMISSION_UUID
from exordos_core.common.constants import ZERO_UUID

ZERO_UUID = str(ZERO_UUID)


def upgrade_admin_organization(session):
    session.execute(
        f"""
        INSERT INTO iam_organizations (
            uuid, status, name, description, created_at, updated_at, info
        )
        SELECT
            '{NEW_ADMIN_ORG_UUID}',
            status,
            name,
            description,
            created_at,
            updated_at,
            info
        FROM iam_organizations
        WHERE uuid = '{ZERO_UUID}'
        ON CONFLICT (uuid) DO NOTHING;
        """
    )
    session.execute(
        f"""
        UPDATE iam_projects
        SET organization = '{NEW_ADMIN_ORG_UUID}'
        WHERE organization = '{ZERO_UUID}';
        """
    )
    session.execute(
        f"""
        DELETE FROM iam_organization_members legacy
        WHERE legacy.organization = '{ZERO_UUID}'
          AND EXISTS (
              SELECT 1
              FROM iam_organization_members current
              WHERE current.organization = '{NEW_ADMIN_ORG_UUID}'
                AND current."user" = legacy."user"
          );
        """
    )
    session.execute(
        f"""
        UPDATE iam_organization_members
        SET organization = '{NEW_ADMIN_ORG_UUID}'
        WHERE organization = '{ZERO_UUID}';
        """
    )
    session.execute(
        f"""
        UPDATE iam_organization_members
        SET uuid = '{ADMIN_ORGANIZATION_MEMBER_UUID}'
        WHERE organization = '{NEW_ADMIN_ORG_UUID}'
          AND "user" = '{ZERO_UUID}';
        """
    )
    session.execute(
        f"""
        UPDATE iam_organization_members
        SET uuid = '{GENESIS_ORGANIZATION_MEMBER_UUID}'
        WHERE organization = '{EXORDOS_CORE_ORGANIZATION_UUID}'
          AND "user" = '{ZERO_UUID}';
        """
    )
    session.execute(
        f"""
        DELETE FROM iam_organizations
        WHERE uuid = '{ZERO_UUID}';
        """
    )


def upgrade_allow_all_permission(session):
    session.execute(
        f"""
        UPDATE iam_permissions
        SET name = '__migrating_allow_all__'
        WHERE uuid = '{ZERO_UUID}'
          AND name = '*.*.*';
        """
    )
    session.execute(
        f"""
        INSERT INTO iam_permissions (uuid, status, name, description, created_at, updated_at)
        SELECT
            '{NEW_ALLOW_ALL_PERMISSION_UUID}',
            status,
            '*.*.*',
            description,
            created_at,
            updated_at
        FROM iam_permissions
        WHERE uuid = '{ZERO_UUID}'
          AND name = '__migrating_allow_all__'
        ON CONFLICT (uuid) DO NOTHING;
        """
    )
    session.execute(
        f"""
        UPDATE iam_binding_permissions
        SET permission = '{NEW_ALLOW_ALL_PERMISSION_UUID}'
        WHERE permission = '{ZERO_UUID}';
        """
    )
    session.execute(
        f"""
        UPDATE iam_binding_permissions
        SET uuid = '{NEW_ALLOW_ALL_BINDING_UUID}'
        WHERE uuid = '{ZERO_UUID}';
        """
    )
    session.execute(
        f"""
        DELETE FROM iam_permissions
        WHERE uuid = '{ZERO_UUID}'
          AND name = '__migrating_allow_all__';
        """
    )


def upgrade_admin_role(session):
    session.execute(
        f"""
        UPDATE iam_roles
        SET name = '__migrating_admin_role__'
        WHERE uuid = '{ZERO_UUID}'
          AND name = 'admin';
        """
    )
    session.execute(
        f"""
        INSERT INTO iam_roles (uuid, status, name, description, project_id, created_at, updated_at)
        SELECT
            '{NEW_ADMIN_ROLE_UUID}',
            status,
            'admin',
            description,
            project_id,
            created_at,
            updated_at
        FROM iam_roles
        WHERE uuid = '{ZERO_UUID}'
          AND name = '__migrating_admin_role__'
        ON CONFLICT (uuid) DO NOTHING;
        """
    )
    session.execute(
        f"""
        UPDATE iam_binding_permissions
        SET role = '{NEW_ADMIN_ROLE_UUID}'
        WHERE role = '{ZERO_UUID}';
        """
    )
    session.execute(
        f"""
        UPDATE iam_binding_roles
        SET role = '{NEW_ADMIN_ROLE_UUID}'
        WHERE role = '{ZERO_UUID}';
        """
    )
    session.execute(
        f"""
        DELETE FROM iam_binding_roles legacy
        WHERE legacy.uuid = '{ZERO_UUID}'
          AND legacy."user" = '{ZERO_UUID}'
          AND legacy.role = '{NEW_ADMIN_ROLE_UUID}'
          AND EXISTS (
              SELECT 1
              FROM iam_binding_roles current
              WHERE current.uuid = '{NEW_ADMIN_ROLE_BINDING_UUID}'
          );
        """
    )
    session.execute(
        f"""
        UPDATE iam_binding_roles
        SET uuid = '{NEW_ADMIN_ROLE_BINDING_UUID}'
        WHERE uuid = '{ZERO_UUID}'
          AND "user" = '{ZERO_UUID}'
          AND role = '{NEW_ADMIN_ROLE_UUID}';
        """
    )
    session.execute(
        f"""
        DELETE FROM iam_roles
        WHERE uuid = '{ZERO_UUID}'
          AND name = '__migrating_admin_role__';
        """
    )


def downgrade_admin_organization(session):
    session.execute(
        f"""
        UPDATE iam_organizations
        SET name = '__downgrading_admin_organization__'
        WHERE uuid = '{NEW_ADMIN_ORG_UUID}';
        """
    )
    session.execute(
        f"""
        INSERT INTO iam_organizations (
            uuid, status, name, description, created_at, updated_at, info
        )
        SELECT
            '{ZERO_UUID}',
            status,
            name,
            description,
            created_at,
            updated_at,
            info
        FROM iam_organizations
        WHERE uuid = '{NEW_ADMIN_ORG_UUID}'
          AND name = '__downgrading_admin_organization__'
        ON CONFLICT (uuid) DO NOTHING;
        """
    )
    session.execute(
        f"""
        UPDATE iam_projects
        SET organization = '{ZERO_UUID}'
        WHERE organization = '{NEW_ADMIN_ORG_UUID}';
        """
    )
    session.execute(
        f"""
        UPDATE iam_organization_members
        SET organization = '{ZERO_UUID}'
        WHERE organization = '{NEW_ADMIN_ORG_UUID}';
        """
    )
    session.execute(
        f"""
        UPDATE iam_organization_members
        SET uuid = gen_random_uuid()
        WHERE uuid IN (
            '{ADMIN_ORGANIZATION_MEMBER_UUID}',
            '{GENESIS_ORGANIZATION_MEMBER_UUID}'
        );
        """
    )
    session.execute(
        f"""
        DELETE FROM iam_organizations
        WHERE uuid = '{NEW_ADMIN_ORG_UUID}';
        """
    )


def downgrade_allow_all_permission(session):
    session.execute(
        f"""
        UPDATE iam_permissions
        SET name = '__downgrading_allow_all__'
        WHERE uuid = '{NEW_ALLOW_ALL_PERMISSION_UUID}';
        """
    )
    session.execute(
        f"""
        INSERT INTO iam_permissions (uuid, name, description)
        VALUES (
            '{ZERO_UUID}',
            '*.*.*',
            'Allow All'
        )
        ON CONFLICT (uuid) DO NOTHING;
        """
    )
    session.execute(
        f"""
        UPDATE iam_binding_permissions
        SET permission = '{ZERO_UUID}'
        WHERE permission = '{NEW_ALLOW_ALL_PERMISSION_UUID}';
        """
    )
    session.execute(
        f"""
        UPDATE iam_binding_permissions
        SET uuid = '{ZERO_UUID}'
        WHERE uuid = '{NEW_ALLOW_ALL_BINDING_UUID}';
        """
    )
    session.execute(
        f"""
        DELETE FROM iam_permissions
        WHERE uuid = '{NEW_ALLOW_ALL_PERMISSION_UUID}';
        """
    )


def downgrade_admin_role(session):
    session.execute(
        f"""
        UPDATE iam_roles
        SET name = '__downgrading_admin_role__'
        WHERE uuid = '{NEW_ADMIN_ROLE_UUID}';
        """
    )
    session.execute(
        f"""
        INSERT INTO iam_roles (uuid, name, description)
        VALUES (
            '{ZERO_UUID}',
            'admin',
            'Admin Role'
        )
        ON CONFLICT (uuid) DO NOTHING;
        """
    )
    session.execute(
        f"""
        UPDATE iam_binding_permissions
        SET role = '{ZERO_UUID}'
        WHERE role = '{NEW_ADMIN_ROLE_UUID}';
        """
    )
    session.execute(
        f"""
        UPDATE iam_binding_roles
        SET role = '{ZERO_UUID}'
        WHERE role = '{NEW_ADMIN_ROLE_UUID}';
        """
    )
    session.execute(
        f"""
        UPDATE iam_binding_roles
        SET uuid = '{ZERO_UUID}'
        WHERE uuid = '{NEW_ADMIN_ROLE_BINDING_UUID}';
        """
    )
    session.execute(
        f"""
        DELETE FROM iam_roles
        WHERE uuid = '{NEW_ADMIN_ROLE_UUID}';
        """
    )


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0000-squashed-current-7f2e4a.py"]

    @property
    def migration_id(self):
        return "5a7a0eb1-b282-4d7b-a70f-58394e112f93"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        upgrade_admin_organization(session)
        upgrade_allow_all_permission(session)
        upgrade_admin_role(session)

    def downgrade(self, session):
        downgrade_admin_role(session)
        downgrade_allow_all_permission(session)
        downgrade_admin_organization(session)


migration_step = MigrationStep()
