#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License")

from restalchemy.storage.sql import migrations


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0017-rawstor-storage-0b42fe.py"]

    @property
    def migration_id(self):
        return "68ba8e2d-14f3-4c28-b264-1e425879217a"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        session.execute(
            "ALTER TABLE storage_clusters "
            "ADD COLUMN cpu integer NOT NULL DEFAULT 2 CHECK (cpu > 0), "
            "ADD COLUMN ram integer NOT NULL DEFAULT 2048 CHECK (ram > 0)"
        )

    def downgrade(self, session):
        session.execute("ALTER TABLE storage_clusters DROP COLUMN cpu, DROP COLUMN ram")


migration_step = MigrationStep()
