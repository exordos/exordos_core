# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0

from restalchemy.storage.sql import migrations


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0014-routed-networks-a7162c.py"]

    @property
    def migration_id(self):
        return "4c2d9e18-3859-41e5-87ad-a78603b19d98"

    def upgrade(self, session):
        session.execute("ALTER TABLE nodes ADD COLUMN power_state varchar(16) NOT NULL DEFAULT 'ON'")

    def downgrade(self, session):
        session.execute("ALTER TABLE nodes DROP COLUMN power_state")


migration_step = MigrationStep()
