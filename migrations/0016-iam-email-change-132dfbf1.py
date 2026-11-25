#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License").

from restalchemy.storage.sql import migrations


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0015-element-manifest-tags-324ac8.py"]

    @property
    def migration_id(self):
        return "132dfbf1-b803-4b2c-8a6c-a51fac40309e"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        session.execute("""
            ALTER TABLE public.iam_users
                ADD COLUMN pending_email character varying(128),
                ADD COLUMN email_change_code_hash character varying(64),
                ADD COLUMN email_change_requested_at timestamp without time zone
        """)

    def downgrade(self, session):
        session.execute("""
            ALTER TABLE public.iam_users
                DROP COLUMN email_change_requested_at,
                DROP COLUMN email_change_code_hash,
                DROP COLUMN pending_email
        """)


migration_step = MigrationStep()
