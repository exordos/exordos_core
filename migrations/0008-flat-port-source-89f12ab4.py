# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0

from restalchemy.storage.sql import migrations


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0007-merge-token-sdn-9673ab8e.py"]

    @property
    def migration_id(self):
        return "89f12ab4-4148-4a60-a44e-66c3f4c9b046"

    def upgrade(self, session):
        session.execute(
            """WITH repaired AS (
                UPDATE compute_ports AS port
                SET source = subnet.name, updated_at = NOW()
                FROM compute_subnets AS subnet
                JOIN compute_networks AS network ON network.uuid = subnet.network
                WHERE port.subnet = subnet.uuid AND port.source IS NULL
                  AND network.driver_spec->>'driver' = 'flat_bridge'
                RETURNING port.machine, port.node
            )
            UPDATE machines SET updated_at = NOW()
            WHERE uuid IN (SELECT machine FROM repaired)
               OR node IN (SELECT node FROM repaired)"""
        )

    def downgrade(self, session):
        pass


migration_step = MigrationStep()
