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


class MigrationStep(migrations.AbstarctMigrationStep):
    def __init__(self):
        self._depends = ["0063-add-driver-spec-connection-uri-unique-index-eb26ec.py"]

    @property
    def migration_id(self):
        return "6451425f-18b1-49c4-b5f8-94702141e880"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        session.execute(
            """
                CREATE TABLE repo_repositories (
                    uuid          UUID PRIMARY KEY,
                    name          VARCHAR(255) NOT NULL,
                    description   TEXT NOT NULL DEFAULT '',
                    project_id    UUID NOT NULL,
                    status        VARCHAR(32) NOT NULL DEFAULT 'NEW',
                    priority      INT NOT NULL DEFAULT 2048,
                    refresh_rate  INT NOT NULL DEFAULT 3600,
                    sync_mode     VARCHAR(32) NOT NULL DEFAULT 'copy',
                    driver_spec   JSONB,
                    next_refresh  TIMESTAMP(6) NOT NULL DEFAULT NOW(),
                    "created_at"  TIMESTAMP(6) NOT NULL DEFAULT NOW(),
                    "updated_at"  TIMESTAMP(6) NOT NULL DEFAULT NOW()
                );

                CREATE INDEX ON repo_repositories(project_id);
                CREATE INDEX ON repo_repositories(status);
                CREATE UNIQUE INDEX ON repo_repositories(project_id, name);
                CREATE UNIQUE INDEX ON repo_repositories(driver_spec);

                CREATE TABLE repo_elements (
                    uuid          UUID PRIMARY KEY,
                    name          VARCHAR(255) NOT NULL,
                    description   TEXT NOT NULL DEFAULT '',
                    project_id    UUID NOT NULL,
                    repository    UUID NOT NULL REFERENCES repo_repositories(uuid) ON DELETE RESTRICT,
                    version       VARCHAR(255) NOT NULL,
                    status        VARCHAR(32) NOT NULL DEFAULT 'NEW',
                    installation_state VARCHAR(32) NOT NULL DEFAULT 'UNINSTALLED',
                    manifest      JSONB DEFAULT '{}',
                    specification JSONB DEFAULT '{}',
                    inventory     JSONB DEFAULT '{}',
                    element       UUID REFERENCES em_elements(uuid) ON DELETE SET NULL,
                    "created_at"  TIMESTAMP(6) NOT NULL DEFAULT NOW(),
                    "updated_at"  TIMESTAMP(6) NOT NULL DEFAULT NOW()
                );

                CREATE INDEX ON repo_elements(project_id);
                CREATE INDEX ON repo_elements(repository);
                CREATE INDEX ON repo_elements(status);
                CREATE INDEX ON repo_elements(name);
                CREATE INDEX ON repo_elements(installation_state);
                CREATE INDEX ON repo_elements(name, installation_state);
                CREATE UNIQUE INDEX ON repo_elements(repository, name, version);

                CREATE TABLE repo_artifacts (
                    uuid          UUID PRIMARY KEY,
                    project_id    UUID NOT NULL,
                    element       UUID NOT NULL REFERENCES repo_elements(uuid) ON DELETE CASCADE,
                    urn           VARCHAR(2048) NOT NULL,
                    uri           VARCHAR(2048) NOT NULL
                );

                CREATE INDEX ON repo_artifacts(project_id);
                CREATE INDEX ON repo_artifacts(element);
                CREATE UNIQUE INDEX ON repo_artifacts(element, urn);

                CREATE TABLE repo_element_deps_bindings (
                    uuid          UUID PRIMARY KEY,
                    element       UUID NOT NULL REFERENCES repo_elements(uuid) ON DELETE CASCADE,
                    depends_on    UUID NOT NULL REFERENCES repo_elements(uuid) ON DELETE CASCADE
                );

                CREATE INDEX ON repo_element_deps_bindings(element);
                CREATE INDEX ON repo_element_deps_bindings(depends_on);
                CREATE UNIQUE INDEX ON repo_element_deps_bindings(element, depends_on);
            """
        )

    def downgrade(self, session):
        self._delete_table_if_exists(session, "repo_element_deps_bindings")
        self._delete_table_if_exists(session, "repo_artifacts")
        self._delete_table_if_exists(session, "repo_elements")
        self._delete_table_if_exists(session, "repo_repositories")


migration_step = MigrationStep()
