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


class MigrationStep(migrations.AbstarctMigrationStep):
    def __init__(self):
        self._depends = ["0004-add-opaque-secrets-362f6d21.py"]

    @property
    def migration_id(self):
        return "5c39d9a1-5af1-4d99-b11c-7c2f88808329"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        session.execute("""
            CREATE TABLE public.iam_service_authorization_codes (
                uuid uuid PRIMARY KEY,
                "user" uuid NOT NULL REFERENCES public.iam_users(uuid) ON DELETE CASCADE,
                iam_client uuid NOT NULL REFERENCES public.iam_clients(uuid) ON DELETE CASCADE,
                project uuid NOT NULL REFERENCES public.iam_projects(uuid) ON DELETE CASCADE,
                code_hash character varying(64) NOT NULL UNIQUE,
                expiration_at timestamp without time zone NOT NULL
            )
        """)

    def downgrade(self, session):
        session.execute("DROP TABLE public.iam_service_authorization_codes")


migration_step = MigrationStep()
