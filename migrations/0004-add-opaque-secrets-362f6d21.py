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

DOWNGRADE = [
    "DROP TABLE IF EXISTS public.storage_secrets",
    "DROP INDEX IF EXISTS public.idx_secret_secrets_tags",
    "DROP TABLE IF EXISTS public.secret_secrets",
]


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
