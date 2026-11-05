# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0

from restalchemy.storage.sql import migrations


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0011-paas-networking-485e71.py"]

    @property
    def migration_id(self):
        return "c3a4f1e9-65dd-4b3d-90ea-ab76823db6ce"

    def upgrade(self, session):
        session.execute(
            "UPDATE ua_target_resources SET updated_at = NOW() WHERE kind = 'evpn_host'"
        )

    def downgrade(self, session):
        session.execute(
            "UPDATE ua_target_resources SET updated_at = NOW() WHERE kind = 'evpn_host'"
        )


migration_step = MigrationStep()
