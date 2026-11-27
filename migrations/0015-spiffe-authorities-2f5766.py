#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License")

from restalchemy.storage.sql import migrations


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0014-manifest-description-4e83cda1.py"]

    @property
    def migration_id(self):
        return "2f5766f8-29de-4c15-9758-78b53d6220ca"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        session.execute(
            """CREATE TABLE storage_spiffe_authorities (
                uuid uuid PRIMARY KEY,
                project_id uuid NOT NULL,
                trust_domain varchar(255) NOT NULL,
                key varchar(10240) NOT NULL,
                cert varchar(10240) NOT NULL,
                UNIQUE(project_id, trust_domain)
            )""",
            None,
        )
    def downgrade(self, session):
        session.execute(
            "LOCK TABLE storage_spiffe_authorities IN ACCESS EXCLUSIVE MODE", None
        )
        session.execute(
            """DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM storage_spiffe_authorities) THEN
                    RAISE EXCEPTION 'Cannot downgrade SPIFFE: signing authorities still exist';
                END IF;
            END $$""",
            None,
        )
        session.execute("DROP TABLE storage_spiffe_authorities", None)


migration_step = MigrationStep()
