#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License")

from restalchemy.storage.sql import migrations


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0015-rawstor-storage-0b42fe.py"]

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
        session.execute(
            """CREATE FUNCTION enforce_spiffe_authority_limit() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                -- Serialize inserts for a project, including different domains.
                PERFORM pg_advisory_xact_lock(hashtextextended('spiffe-authorities/' || NEW.project_id::text, 0));
                IF (SELECT count(*) FROM storage_spiffe_authorities
                    WHERE project_id = NEW.project_id) >= 32 THEN
                    RAISE EXCEPTION 'SPIFFE authority limit (32) exceeded for project %', NEW.project_id
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END
            $$""",
            None,
        )
        session.execute(
            """CREATE TRIGGER spiffe_authority_limit BEFORE INSERT
            ON storage_spiffe_authorities FOR EACH ROW
            EXECUTE FUNCTION enforce_spiffe_authority_limit()""",
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
        session.execute("DROP FUNCTION enforce_spiffe_authority_limit()", None)


migration_step = MigrationStep()
