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

from restalchemy.storage.sql import migrations

from exordos_core.common.constants import OWNER_ROLE_UUID

# The core manifest declares these permissions and bindings, but
# ec-bootstrap skips reapplying the core manifest once its RepoElement is
# ACTIVE, so an already installed stand never seeds them. Seed them here so
# the upgrade path reaches them too.
#
# The UUIDs are the ones the manifest declares: iam_permissions.name is
# unique, so a row seeded under a different UUID collides with the manifest
# on a fresh installation.
SECRET_PERMISSIONS = [
    (
        "0452063a-8bb7-4433-a8ee-a3fec331d43b",
        "secret.secret.create",
        "Create secrets",
    ),
    (
        "ade9d7d9-8972-4c41-b3d7-3e2eee86d097",
        "secret.secret.delete",
        "Delete secrets",
    ),
    (
        "a2587371-ba16-4d69-982b-f7321824b273",
        "secret.secret.read",
        "List and read secrets",
    ),
    (
        "1f6d73f4-0611-48f8-88dd-94a3c2b9022d",
        "secret.secret.update",
        "Update secrets",
    ),
]

# The owner role gets read and update, the two bindings the manifest
# declares. They carry no project_id there, so these rows leave it NULL.
OWNER_BINDINGS = [
    (
        "4f3a8e13-9bea-52f2-b2ce-3acb390a8d99",
        "a2587371-ba16-4d69-982b-f7321824b273",
    ),
    (
        "19227824-60dd-5e56-8bfd-dd06fde8c9ba",
        "1f6d73f4-0611-48f8-88dd-94a3c2b9022d",
    ),
]

UPGRADE = [
    """
    CREATE TABLE public.secret_secrets (
        uuid uuid NOT NULL,
        name character varying(255) NOT NULL,
        description character varying(255) NOT NULL,
        project_id uuid NOT NULL,
        status public.enum_secret_status
            DEFAULT 'NEW'::public.enum_secret_status NOT NULL,
        constructor jsonb NOT NULL,
        value character varying(10240),
        default_value character varying(10240),
        tags TEXT[] NOT NULL DEFAULT '{}',
        created_at timestamp without time zone
            DEFAULT CURRENT_TIMESTAMP NOT NULL,
        updated_at timestamp without time zone
            DEFAULT CURRENT_TIMESTAMP NOT NULL
    )
    """,
    """
    ALTER TABLE ONLY public.secret_secrets
        ADD CONSTRAINT secret_secrets_pkey PRIMARY KEY (uuid)
    """,
    "CREATE INDEX idx_secret_secrets_tags ON public.secret_secrets USING GIN (tags)",
    """
    CREATE INDEX secret_secrets_project_id_idx
        ON public.secret_secrets USING btree (project_id)
    """,
    """
    CREATE TABLE public.storage_secrets (
        uuid uuid NOT NULL,
        status public.enum_secret_status
            DEFAULT 'NEW'::public.enum_secret_status NOT NULL,
        value character varying(10240) NOT NULL,
        meta jsonb NOT NULL,
        created_at timestamp without time zone
            DEFAULT CURRENT_TIMESTAMP NOT NULL,
        updated_at timestamp without time zone
            DEFAULT CURRENT_TIMESTAMP NOT NULL
    )
    """,
    """
    ALTER TABLE ONLY public.storage_secrets
        ADD CONSTRAINT storage_secrets_pkey PRIMARY KEY (uuid)
    """,
]

UPGRADE += [
    f"""
    INSERT INTO iam_permissions (uuid, name, description)
    VALUES ('{permission_uuid}', '{name}', '{description}')
    ON CONFLICT (uuid) DO NOTHING
    """
    for permission_uuid, name, description in SECRET_PERMISSIONS
]

# Both orderings have to converge on a single row. On a fresh installation
# the migration runs first and the manifest then declares these same UUIDs;
# on a stand that did reapply the manifest the binding is already there
# under a UUID of its own, which only the pair guard catches.
UPGRADE += [
    f"""
    INSERT INTO iam_binding_permissions (uuid, role, permission)
    SELECT '{binding_uuid}', '{OWNER_ROLE_UUID}', '{permission_uuid}'
    WHERE NOT EXISTS (
        SELECT 1 FROM iam_binding_permissions
        WHERE role = '{OWNER_ROLE_UUID}'
          AND permission = '{permission_uuid}'
    )
    """
    for binding_uuid, permission_uuid in OWNER_BINDINGS
]

DOWNGRADE = [
    "DROP TABLE IF EXISTS public.storage_secrets",
    "DROP INDEX IF EXISTS public.secret_secrets_project_id_idx",
    "DROP INDEX IF EXISTS public.idx_secret_secrets_tags",
    "DROP TABLE IF EXISTS public.secret_secrets",
]

# The bindings reference the permissions, so they have to go first.
DOWNGRADE = (
    [
        f"DELETE FROM iam_binding_permissions WHERE permission = '{permission_uuid}'"
        for permission_uuid, _, _ in SECRET_PERMISSIONS
    ]
    + [
        f"DELETE FROM iam_permissions WHERE uuid = '{permission_uuid}'"
        for permission_uuid, _, _ in SECRET_PERMISSIONS
    ]
    + DOWNGRADE
)


class MigrationStep(migrations.AbstarctMigrationStep):
    def __init__(self):
        self._depends = ["0003-add-tags-to-models-9e4b2d1f.py"]

    @property
    def migration_id(self):
        return "362f6d21-7587-46f0-8179-d062f483498c"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        for statement in UPGRADE:
            session.execute(statement)

    def downgrade(self, session):
        for statement in DOWNGRADE:
            session.execute(statement)


migration_step = MigrationStep()
