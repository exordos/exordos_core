# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0

from restalchemy.storage.sql import migrations


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = [
            "0005-add-managed-tokens-5b1e8c47.py",
            "0006-sdn-api-contract-15df7a.py",
        ]

    @property
    def migration_id(self):
        return "9673ab8e-993d-437a-a2d8-d88e8226069a"

    def upgrade(self, session):
        pass

    def downgrade(self, session):
        pass


migration_step = MigrationStep()
