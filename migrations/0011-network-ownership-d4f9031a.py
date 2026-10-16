# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0

from restalchemy.storage.sql import migrations


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0010-default-network-a182bc39.py"]

    @property
    def migration_id(self):
        return "d4f9031a-ce23-4f45-ae94-410d79f657f5"

    def upgrade(self, session):
        session.execute("ALTER TABLE compute_ports ADD COLUMN owner_node UUID")
        session.execute(
            "UPDATE compute_ports SET owner_node = node WHERE provenance = 'generated'"
        )
        session.execute(
            "UPDATE compute_ports p SET legacy_flat_placement = TRUE "
            "FROM compute_subnets s JOIN compute_networks n ON n.uuid = s.network "
            "WHERE p.subnet = s.uuid AND p.provenance = 'generated' "
            "AND n.driver_spec->>'driver' = 'flat_bridge'"
        )
        session.execute(
            "CREATE INDEX compute_ports_owner_node_idx ON compute_ports(owner_node) "
            "WHERE owner_node IS NOT NULL"
        )
        session.execute("ALTER TABLE compute_ports DROP COLUMN provenance")
        session.execute("UPDATE compute_sets SET updated_at = NOW()")

    def downgrade(self, session):
        session.execute(
            "ALTER TABLE compute_ports ADD COLUMN provenance VARCHAR(16) NOT NULL DEFAULT 'user'"
        )
        session.execute(
            "UPDATE compute_ports SET provenance = 'generated' WHERE owner_node IS NOT NULL"
        )
        session.execute("ALTER TABLE compute_ports DROP COLUMN owner_node")


migration_step = MigrationStep()
