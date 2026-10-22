# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0.

import uuid

from restalchemy.storage.sql import migrations

PERMISSION_UUID = str(
    uuid.uuid5(
        uuid.UUID("dfd0c604-607f-4260-981f-374f88435ea0"),
        "network.port.use_foreign_subnet",
    )
)


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0010-network-sdn-9e41c7.py"]

    @property
    def migration_id(self):
        return "485e71b6-7f44-4eab-9f11-280fbd196889"

    def upgrade(self, session):
        session.execute(
            "ALTER TABLE compute_sets ADD COLUMN network_subnet UUID REFERENCES compute_subnets(uuid)"
        )
        session.execute("""UPDATE compute_sets AS s SET network_subnet = existing.subnet, updated_at = NOW()
            FROM (SELECT n.node_set, min(p.subnet::text)::uuid AS subnet
                  FROM nodes n JOIN compute_ports p ON p.node = n.uuid AND p.index = 0
                  WHERE n.node_set IS NOT NULL GROUP BY n.node_set
                  HAVING count(DISTINCT p.subnet) = 1) AS existing
            WHERE s.uuid = existing.node_set""")
        session.execute(
            "INSERT INTO iam_permissions (uuid, name, description) VALUES (%s, %s, %s)",
            (
                PERMISSION_UUID,
                "network.port.use_foreign_subnet",
                "Connect service-owned ports to private subnets of other projects",
            ),
        )

    def downgrade(self, session):
        session.execute("ALTER TABLE compute_sets DROP COLUMN network_subnet")
        session.execute(
            "DELETE FROM iam_binding_permissions WHERE permission = %s",
            (PERMISSION_UUID,),
        )
        session.execute(
            "DELETE FROM iam_permissions WHERE uuid = %s", (PERMISSION_UUID,)
        )


migration_step = MigrationStep()
