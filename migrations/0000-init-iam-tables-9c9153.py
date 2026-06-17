# Copyright 2016 Eugene Frolov <eugene@frolov.net.ru>
# Copyright 2025 Genesis Corporation
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

import base64
import hashlib
import os

from restalchemy.storage.sql import migrations


class MigrationStep(migrations.AbstarctMigrationStep):
    def __init__(self):
        self._depends = [
            "0000-root-d34de1.py",
        ]
        self._global_salt = os.getenv(
            "GLOBAL_SALT",
            "FOy/2kwwdn0ig1QOq7cestqe",
        )
        self._default_client_secret = os.getenv(
            "DEFAULT_CLIENT_SECRET",
            "GenesisCoreSecret",
        )
        self._admin_password = os.getenv(
            "ADMIN_PASSWORD",
            "admin",
        )

    @property
    def migration_id(self):
        return "9c9153e3-e187-4420-bf4d-d0e4dbf489d3"

    @property
    def is_manual(self):
        return False

    def _generate_hash(cls, secret, secret_salt, global_salt):

        raw_secret_salt = base64.b64decode(secret_salt)
        raw_global_salt = base64.b64decode(global_salt)

        hashed = hashlib.pbkdf2_hmac(
            "sha512",
            secret.encode("utf-8"),
            raw_secret_salt + raw_global_salt,
            251685,  # count of iterations
        )

        return hashed.hex()

    def _get_admin_secret(self):
        return self._generate_hash(
            secret=self._admin_password,
            secret_salt=self._global_salt,
            global_salt=self._global_salt,
        )

    def _get_client_secret(self):
        return None

    def upgrade(self, session):
        default_admin_salt = "d4JJ9QYuEEJxHCFja9FZskG4"
        default_admin_secret = self._generate_hash(
            secret=self._admin_password,
            secret_salt=default_admin_salt,
            global_salt=self._global_salt,
        )
        default_client_secret = self._generate_hash(
            secret=self._default_client_secret,
            secret_salt=default_admin_salt,
            global_salt=self._global_salt,
        )

        expressions = [
            # Users
            """
                CREATE TABLE IF NOT EXISTS "iam_users" (
                    "uuid" UUID PRIMARY KEY,
                    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                        CHECK (status IN ('ACTIVE')),
                    "name" VARCHAR(256) NOT NULL,
                    "description" VARCHAR(256) NOT NULL,
                    "first_name" VARCHAR(128) NOT NULL,
                    "last_name" VARCHAR(128) NOT NULL,
                    "email" VARCHAR(128) NOT NULL,
                    "secret_hash" CHAR(128) NOT NULL,
                    "salt" CHAR(24) NOT NULL,
                    "otp_secret" VARCHAR(128) DEFAULT '',
                    "otp_enabled" BOOLEAN DEFAULT FALSE,
                    "created_at" TIMESTAMP(6) NOT NULL DEFAULT NOW(),
                    "updated_at" TIMESTAMP(6) NOT NULL DEFAULT NOW()
                );
            """,
            """
                CREATE UNIQUE INDEX "iam_users_name_idx" ON "iam_users" (
                    "name"
                );
            """,
            """
                CREATE UNIQUE INDEX "iam_users_email_idx" ON "iam_users" (
                    "email"
                );
            """,
            f"""
                INSERT INTO "iam_users" (
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
                );
            """,
            # Organizations
            """
                CREATE TABLE IF NOT EXISTS "iam_organizations" (
                    "uuid" UUID PRIMARY KEY,
                    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                        CHECK (status IN ('ACTIVE')),
                    "name" VARCHAR(128) NOT NULL,
                    "description" VARCHAR(256) DEFAULT '',
                    "owner" UUID NOT NULL REFERENCES "iam_users" ("uuid")
                        ON DELETE RESTRICT
                        ON UPDATE RESTRICT,
                    "created_at" TIMESTAMP(6) NOT NULL DEFAULT NOW(),
                    "updated_at" TIMESTAMP(6) NOT NULL DEFAULT NOW()
                );
            """,
            """
                CREATE INDEX "iam_organizations_name_idx" ON
                    "iam_organizations" ("name");
            """,
            """
                INSERT INTO "iam_organizations" (
                    "uuid", "name", "description", "owner"
                ) VALUES (
                    '00000000-0000-0000-0000-000000000000',
                    'admin', 'Admin Organization',
                    '00000000-0000-0000-0000-000000000000'
                );
            """,
            # Projects
            """
                CREATE TABLE IF NOT EXISTS "iam_projects" (
                    "uuid" UUID PRIMARY KEY,
                    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                        CHECK (
                            status IN (
                                'NEW',
                                'IN_PROGRESS',
                                'ACTIVE',
                                'DELETING'
                            )
                        ),
                    "name" VARCHAR(128) NOT NULL,
                    "description" VARCHAR(256) DEFAULT '',
                    "organization" UUID NOT NULL REFERENCES
                        "iam_organizations" ("uuid"),
                    "created_at" TIMESTAMP(6) NOT NULL DEFAULT NOW(),
                    "updated_at" TIMESTAMP(6) NOT NULL DEFAULT NOW()
                );
            """,
            """
                CREATE INDEX "iam_projects_name_idx" ON "iam_projects"
                    ("name");
            """,
            """
                CREATE INDEX "iam_projects_organization_idx" ON
                    "iam_projects" ("organization");
            """,
            """
                INSERT INTO "iam_projects" (
                    "uuid", "name", description, organization
                ) VALUES (
                    '00000000-0000-0000-0000-000000000000',
                    'admin', 'Admin Project',
                    '00000000-0000-0000-0000-000000000000'
                );
            """,
            # Roles
            """
                CREATE TABLE IF NOT EXISTS "iam_roles" (
                    "uuid" UUID PRIMARY KEY,
                    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                        CHECK (status IN ('ACTIVE')),
                    "name" VARCHAR(128) NOT NULL,
                    "description" VARCHAR(256) DEFAULT '',
                    "project_id" UUID DEFAULT NULL,
                    "created_at" TIMESTAMP(6) NOT NULL DEFAULT NOW(),
                    "updated_at" TIMESTAMP(6) NOT NULL DEFAULT NOW()
                );
            """,
            """
                CREATE INDEX "iam_roles_name_idx" ON "iam_roles" ("name");
            """,
            """
                INSERT INTO "iam_roles" (
                    "uuid", "name", "description"
                ) VALUES (
                    '00000000-0000-0000-0000-000000000000',
                    'admin', 'Admin Role'
                );
            """,
            # Permissions
            """
                CREATE TABLE IF NOT EXISTS "iam_permissions" (
                    "uuid" UUID PRIMARY KEY,
                    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                        CHECK (status IN ('ACTIVE')),
                    "name" VARCHAR(256) NOT NULL,
                    "description" VARCHAR(256) DEFAULT '',
                    "created_at" TIMESTAMP(6) NOT NULL DEFAULT NOW(),
                    "updated_at" TIMESTAMP(6) NOT NULL DEFAULT NOW()
                );
            """,
            """
                CREATE UNIQUE INDEX "iam_permissions_name_idx" ON
                    "iam_permissions" ("name");
            """,
            """
            INSERT INTO "iam_permissions" (
                "uuid", "name", "description"
            ) VALUES (
                '00000000-0000-0000-0000-000000000000',
                '*.*.*', 'Allow All'
            );
            """,
            # Bindings
            """
                CREATE TABLE IF NOT EXISTS "iam_binding_permissions" (
                    "uuid" UUID PRIMARY KEY,
                    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                        CHECK (status IN ('ACTIVE')),
                    "project_id" UUID DEFAULT NULL,
                    "role" UUID NOT NULL REFERENCES "iam_roles" ("uuid"),
                    "permission" UUID NOT NULL REFERENCES
                        "iam_permissions" ("uuid"),
                    "description" VARCHAR(256) DEFAULT '',
                    "created_at" TIMESTAMP(6) NOT NULL DEFAULT NOW(),
                    "updated_at" TIMESTAMP(6) NOT NULL DEFAULT NOW()
                );
            """,
            """
                CREATE INDEX "iam_binding_permissions_role_permission_idx" ON
                    "iam_binding_permissions" ("role", "permission");
            """,
            """
                INSERT INTO "iam_binding_permissions" (
                    "uuid", "role", "permission"
                ) VALUES (
                    '00000000-0000-0000-0000-000000000000',
                    '00000000-0000-0000-0000-000000000000',
                    '00000000-0000-0000-0000-000000000000'
                );
            """,
            """
                CREATE TABLE IF NOT EXISTS "iam_binding_roles" (
                    "uuid" UUID PRIMARY KEY,
                    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                        CHECK (status IN ('ACTIVE')),
                    "user" UUID NOT NULL REFERENCES "iam_users" ("uuid"),
                    "role" UUID NOT NULL REFERENCES "iam_roles" ("uuid"),
                    "project" UUID DEFAULT NULL REFERENCES
                        "iam_projects" ("uuid"),
                    "description" VARCHAR(256) DEFAULT '',
                    "created_at" TIMESTAMP(6) NOT NULL DEFAULT NOW(),
                    "updated_at" TIMESTAMP(6) NOT NULL DEFAULT NOW()
                );
            """,
            """
                CREATE INDEX "iam_binding_roles_user_idx" ON
                    "iam_binding_roles" ("user");
            """,
            """
                CREATE INDEX "iam_binding_roles_role_idx" ON
                    "iam_binding_roles" ("role");
            """,
            """
                CREATE INDEX "iam_binding_roles_project_idx" ON
                    "iam_binding_roles" ("project");
            """,
            """
                INSERT INTO "iam_binding_roles" (
                    "uuid", "user", "role", "description"
                ) VALUES (
                    '00000000-0000-0000-0000-000000000000',
                    '00000000-0000-0000-0000-000000000000',
                    '00000000-0000-0000-0000-000000000000',
                    'Super Administrator'
                );
            """,
            # IDP
            """
                CREATE TABLE IF NOT EXISTS "iam_idp" (
                    "uuid" UUID PRIMARY KEY,
                    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                        CHECK (status IN ('ACTIVE')),
                    "name" VARCHAR(256) NOT NULL,
                    "project_id" UUID DEFAULT NULL,
                    "description" VARCHAR(256) DEFAULT '',
                    "client_id" VARCHAR(64) NOT NULL,
                    "secret_hash" CHAR(128) NOT NULL,
                    "salt" CHAR(24) NOT NULL,
                    "scope" VARCHAR(64) DEFAULT 'openid',
                    "well_known_endpoint" VARCHAR(256) NOT NULL,
                    "redirect_uri_template" VARCHAR(256) NOT NULL,
                    "created_at" TIMESTAMP(6) NOT NULL DEFAULT NOW(),
                    "updated_at" TIMESTAMP(6) NOT NULL DEFAULT NOW()
                );
            """,
            """
                CREATE UNIQUE INDEX "iam_idp_id_idx" ON "iam_idp" (
                    "client_id"
            )
            """,
            # Clients
            """
                CREATE TABLE IF NOT EXISTS "iam_clients" (
                    "uuid" UUID PRIMARY KEY,
                    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                        CHECK (status IN ('ACTIVE')),
                    "name" VARCHAR(256) NOT NULL,
                    "project_id" UUID DEFAULT NULL,
                    "description" VARCHAR(256) DEFAULT '',
                    "client_id" VARCHAR(64) NOT NULL,
                    "secret_hash" CHAR(128) NOT NULL,
                    "salt" CHAR(24) NOT NULL,
                    "redirect_url" VARCHAR(256) NOT NULL,
                    "created_at" TIMESTAMP(6) NOT NULL DEFAULT NOW(),
                    "updated_at" TIMESTAMP(6) NOT NULL DEFAULT NOW()
                );
            """,
            """
                CREATE UNIQUE INDEX "iam_client_id_idx" ON "iam_clients" (
                    "client_id"
            )""",
            f"""
                INSERT INTO "iam_clients" (
                    "uuid", "name", "description", "client_id",
                    "secret_hash", "salt", "redirect_url"
                ) VALUES(
                    '00000000-0000-0000-0000-000000000000',
                    'GenesisCoreClient',
                    'Exordos Core OIDC Client',
                    'GenesisCoreClientId',
                    '{default_client_secret}',
                    '{default_admin_salt}',
                    'http://127.0.0.1:11010/v1/'
                );
            """,
            # Tokens
            """
                CREATE TABLE IF NOT EXISTS "iam_tokens" (
                    "uuid" UUID PRIMARY KEY,
                    "user" UUID NOT NULL REFERENCES "iam_users" ("uuid")
                        ON DELETE CASCADE
                        ON UPDATE CASCADE,
                    "project" UUID DEFAULT NULL REFERENCES "iam_projects"
                        ("uuid")
                        ON DELETE CASCADE
                        ON UPDATE CASCADE,
                    "experation_at" TIMESTAMP(6) NOT NULL,
                    "refresh_token_uuid" UUID NOT NULL,
                    "refresh_experation_at" TIMESTAMP(6) NOT NULL,
                    "issuer" VARCHAR(256) DEFAULT NULL,
                    "audience" VARCHAR(256) DEFAULT 'account',
                    "typ" VARCHAR(64) DEFAULT 'Bearer',
                    "scope" VARCHAR(128) NOT NULL,
                    "created_at" TIMESTAMP(6) NOT NULL DEFAULT NOW(),
                    "updated_at" TIMESTAMP(6) NOT NULL DEFAULT NOW()
                );
            """,
            """
                CREATE OR REPLACE VIEW "iam_permissions_fast_view" AS
                    SELECT
                        "t1"."uuid" AS "uuid",
                        "t1"."uuid" AS "permission",
                        "t4"."uuid" AS "user",
                        "t3"."uuid" AS "role",
                        "t3"."project" as "project"
                    FROM
                        "iam_permissions" AS "t1"
                    LEFT JOIN
                        "iam_binding_permissions" AS "t2"
                        ON ("t2"."permission" = "t1"."uuid")
                    LEFT JOIN
                        "iam_binding_roles" AS "t3"
                        ON ("t3"."role" = "t2"."role")
                    LEFT JOIN
                        "iam_users" AS "t4"
                        ON ("t4"."uuid" = "t3"."user");
            """,
        ]

        for expression in expressions:
            session.execute(expression)

    def downgrade(self, session):
        tables = [
            "iam_tokens",
            "iam_clients",
            "iam_idp",
            "iam_binding_roles",
            "iam_binding_permissions",
            "iam_permissions",
            "iam_roles",
            "iam_users",
            "iam_projects",
            "iam_organizations",
        ]

        views = [
            "iam_permissions_fast_view",
        ]

        for view in views:
            self._delete_view_if_exists(session, view)

        for table in tables:
            self._delete_table_if_exists(session, table)
            # session.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE;')


migration_step = MigrationStep()
