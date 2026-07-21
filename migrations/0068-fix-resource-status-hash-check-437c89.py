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

from restalchemy.storage.sql import migrations


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = [
            "0067-init-border-ec37b4.py",
        ]

    @property
    def migration_id(self):
        return "437c8950-3580-406a-aaae-48f9aeac42c2"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        session.execute(
            """
                CREATE OR REPLACE VIEW "em_incorrect_resource_statuses_view" AS
                SELECT
                    "er"."uuid" AS "uuid",
                    "er"."status" AS "current_status",
                    (
                        CASE
                            WHEN "utr"."hash" IS NULL THEN "uar"."status"
                            WHEN "uar"."status" = 'ACTIVE'
                                AND "utr"."hash" = "uar"."hash" THEN 'ACTIVE'
                            WHEN "uar"."status" IS NULL THEN NULL
                            ELSE 'IN_PROGRESS'
                        END
                    )::varchar(32) AS "actual_status"
                FROM
                    "em_resources" "er"
                LEFT JOIN (
                    SELECT
                        "uuid",
                        "hash"
                    FROM "ua_target_resources"
                    WHERE "kind" LIKE 'em_%'
                ) AS "utr"
                    ON "er"."uuid" = "utr"."uuid"
                LEFT JOIN (
                    SELECT
                        "uuid",
                        "status",
                        "hash"
                    FROM "ua_actual_resources"
                    WHERE "kind" LIKE 'em_%'
                ) AS "uar"
                    ON "er"."uuid" = "uar"."uuid"
                WHERE
                    "er"."status" IS DISTINCT FROM (
                        CASE
                            WHEN "utr"."hash" IS NULL THEN "uar"."status"
                            WHEN "uar"."status" = 'ACTIVE'
                                AND "utr"."hash" = "uar"."hash" THEN 'ACTIVE'
                            WHEN "uar"."status" IS NULL THEN NULL
                            ELSE 'IN_PROGRESS'
                        END
                    )::varchar(32);
            """,
            None,
        )

    def downgrade(self, session):
        session.execute(
            """
                CREATE OR REPLACE VIEW "em_incorrect_resource_statuses_view" AS
                SELECT
                    "er"."uuid" AS "uuid",
                    "er"."status" AS "current_status",
                    "uar"."status" AS "actual_status"
                FROM
                    "em_resources" "er"
                LEFT JOIN (
                    SELECT
                        "uuid",
                        "status"
                    FROM "ua_actual_resources"
                    WHERE "kind" LIKE 'em_%'
                ) AS "uar"
                    ON "er"."uuid" = "uar"."uuid"
                WHERE
                    "er"."status" IS DISTINCT FROM "uar"."status";
            """,
            None,
        )


migration_step = MigrationStep()
