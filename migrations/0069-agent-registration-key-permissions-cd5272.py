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

import uuid as sys_uuid

from restalchemy.storage.sql import migrations

NS_UUID = sys_uuid.UUID("dfd0c604-607f-4260-981f-374f88435ea0")

# UA agent permissions
UA_AGENT_PERMISSIONS = (
    ("agent.ua.create", "Register a Universal Agent"),
    ("agent.ua.issue_key", "Issue or fetch a Universal Agent node encryption key"),
)


def _u(name: str) -> str:
    return str(sys_uuid.uuid5(NS_UUID, name))


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = [
            "0068-fix-resource-status-hash-check-437c89.py",
        ]

    @property
    def migration_id(self):
        return "468b0d16-742f-40a9-a6fe-3df099dae61f"

    @property
    def is_manual(self):
        return False

    def _create_permissions(self, session):
        for name, description in UA_AGENT_PERMISSIONS:
            session.execute(f"""
                INSERT INTO iam_permissions (
                    uuid, name, description
                ) VALUES (
                    '{_u(name)}',
                    '{name}',
                    '{description}'
                )
                ON CONFLICT (uuid) DO NOTHING;
            """)

    def upgrade(self, session):
        self._create_permissions(session)

    def _delete_bindings(self, session):
        for name, _ in UA_AGENT_PERMISSIONS:
            session.execute(f"""
                DELETE FROM iam_binding_permissions
                WHERE permission = '{_u(name)}';
            """)

    def _delete_permissions(self, session):
        for name, _ in UA_AGENT_PERMISSIONS:
            session.execute(f"""
                DELETE FROM iam_permissions
                WHERE uuid = '{_u(name)}';
            """)

    def downgrade(self, session):
        self._delete_bindings(session)
        self._delete_permissions(session)


migration_step = MigrationStep()
