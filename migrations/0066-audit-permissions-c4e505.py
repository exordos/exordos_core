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

import os
import uuid as sys_uuid

from gcl_sdk import migrations as sdk_migrations
from gcl_sdk.common import utils as sdk_utils
from restalchemy.storage.sql import migrations

NS_UUID = sys_uuid.UUID("dfd0c604-607f-4260-981f-374f88435ea0")
OWNER_ROLE_UUID = "726f6c65-0000-0000-0000-000000000002"

AUDIT_READ = "audit.events.read"
AUDIT_READ_ALL = "audit.events.read_all"
AUDIT_CREATE = "audit.events.create"
SDK_MIGRATION_FILE_NAME = "0007-init-audit-events-4f3a2b"
AUDIT_PERMISSIONS = (
    (AUDIT_CREATE, "Create events in the central audit service"),
    (AUDIT_READ, "Read audit events in the current project"),
    (AUDIT_READ_ALL, "Read audit events in all projects"),
)


def _u(name: str) -> str:
    return str(sys_uuid.uuid5(NS_UUID, name))


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0065-registration-client-auto-provision-b7f2d9.py"]

    @property
    def migration_id(self):
        return "c4e5055c-880e-4150-a93e-6759d9277ad1"

    @property
    def is_manual(self):
        return False

    def _get_sdk_migration_engine(self):
        sdk_migration_path = os.path.dirname(sdk_migrations.__file__)
        return sdk_utils.MigrationEngine(migrations_path=sdk_migration_path)

    def _create_permissions(self, session):
        for name, description in AUDIT_PERMISSIONS:
            session.execute(
                """
                INSERT INTO iam_permissions (
                    uuid, name, description
                ) VALUES (
                    %s, %s, %s
                )
                ON CONFLICT (uuid) DO NOTHING;
                """,
                (_u(name), name, description),
            )

    def _create_bindings(self, session):
        session.execute(
            """
                INSERT INTO iam_binding_permissions (
                    uuid, role, permission, project_id
                ) VALUES (
                    gen_random_uuid(), %s, %s, NULL
                );
            """,
            (OWNER_ROLE_UUID, _u(AUDIT_READ)),
        )

    def upgrade(self, session):
        migration_engine = self._get_sdk_migration_engine()
        migration_engine.apply_migration(SDK_MIGRATION_FILE_NAME, session)
        self._create_permissions(session)
        self._create_bindings(session)

    def _delete_bindings(self, session):
        for name, _ in AUDIT_PERMISSIONS:
            session.execute(
                """
                    DELETE FROM iam_binding_permissions
                    WHERE permission = %s;
                """,
                (_u(name),),
            )

    def _delete_permissions(self, session):
        for name, _ in AUDIT_PERMISSIONS:
            session.execute(
                """
                    DELETE FROM iam_permissions
                    WHERE uuid = %s;
                """,
                (_u(name),),
            )

    def downgrade(self, session):
        self._delete_bindings(session)
        self._delete_permissions(session)
        migration_engine = self._get_sdk_migration_engine()
        migration_engine.rollback_migration(SDK_MIGRATION_FILE_NAME, session)


migration_step = MigrationStep()
