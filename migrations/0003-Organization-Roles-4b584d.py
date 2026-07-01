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


class MigrationStep(migrations.AbstarctMigrationStep):
    def __init__(self):
        self._depends = ["0002-add-volumes-tables-a6972c.py"]

    @property
    def migration_id(self):
        return "4b584d08-3345-44f3-8f90-b3caafc7a206"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        expressions = [
            """
                CREATE TABLE IF NOT EXISTS "iam_organization_members" (
                    "uuid" UUID PRIMARY KEY,
                    "organization" UUID NOT NULL REFERENCES
                        "iam_organizations" ("uuid")
                        ON DELETE CASCADE
                        ON UPDATE CASCADE,
                    "user" UUID NOT NULL REFERENCES "iam_users" ("uuid")
                        ON DELETE CASCADE
                        ON UPDATE CASCADE,
                    "role" VARCHAR(20) NOT NULL DEFAULT 'MEMBER'
                        CHECK (role IN ('OWNER', 'MEMBER')),
                    "created_at" TIMESTAMP(6) NOT NULL DEFAULT NOW(),
                    "updated_at" TIMESTAMP(6) NOT NULL DEFAULT NOW(),
                    CONSTRAINT uq_organization_user UNIQUE (
                        "organization", "user"
                    )
                );
            """,
            """
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
                    o."owner",
                    'OWNER',
                    o."created_at",
                    o."updated_at"
                FROM "iam_organizations" o;
            """,
            """
                ALTER TABLE "iam_organizations" DROP COLUMN "owner";
            """,
        ]

        for expression in expressions:
            session.execute(expression)

    def downgrade(self, session):
        expressions = [
            """
                ALTER TABLE "iam_organizations"
                    ADD COLUMN "owner" UUID;
            """,
            """
                UPDATE
                    "iam_organizations" o
                SET "owner" = (
                    SELECT m."user"
                    FROM "iam_organization_members" m
                    WHERE
                        m."organization" = o."uuid"
                        AND m."role" = 'OWNER'
                    LIMIT 1
                );
            """,
            # Organizations without an owner member (e.g. the owner user was
            # deleted) cannot be represented in the old schema, where owner
            # is NOT NULL. Fall back to any member, then to the oldest user.
            """
                UPDATE
                    "iam_organizations" o
                SET "owner" = (
                    SELECT m."user"
                    FROM "iam_organization_members" m
                    WHERE m."organization" = o."uuid"
                    LIMIT 1
                )
                WHERE o."owner" IS NULL;
            """,
            """
                UPDATE
                    "iam_organizations" o
                SET "owner" = (
                    SELECT u."uuid"
                    FROM "iam_users" u
                    ORDER BY u."created_at"
                    LIMIT 1
                )
                WHERE o."owner" IS NULL;
            """,
            """
                ALTER TABLE "iam_organizations"
                    ALTER COLUMN "owner" SET NOT NULL;
            """,
        ]

        for expression in expressions:
            session.execute(expression)

        drop_tables = [
            "iam_organization_members",
        ]

        for table in drop_tables:
            self._delete_table_if_exists(session, table)


migration_step = MigrationStep()
