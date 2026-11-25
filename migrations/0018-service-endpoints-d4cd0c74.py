#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License").

from restalchemy.storage.sql import migrations


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0017-spiffe-authorities-2f5766.py"]

    @property
    def migration_id(self):
        return "d4cd0c74-c6b4-4096-b368-1016a5e975b0"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        session.execute(
            "ALTER TABLE em_services ADD COLUMN endpoints jsonb NOT NULL DEFAULT '{}'"
        )
        session.execute("""
            CREATE TABLE em_endpoint_addresses (
                target uuid NOT NULL,
                service uuid NOT NULL,
                endpoint text NOT NULL,
                port integer NOT NULL CHECK (port BETWEEN 20000 AND 29999),
                PRIMARY KEY (target, service, endpoint),
                UNIQUE (target, port)
            )
        """)
        session.execute("""
            CREATE TABLE em_endpoint_uses (
                config uuid NOT NULL REFERENCES em_resources(uuid) ON DELETE CASCADE,
                consumer uuid NOT NULL,
                consumer_kind text NOT NULL CHECK (consumer_kind IN ('node', 'node_set')),
                service uuid NOT NULL,
                endpoint text NOT NULL,
                PRIMARY KEY (config, service, endpoint),
                FOREIGN KEY (consumer, service, endpoint)
                    REFERENCES em_endpoint_addresses(target, service, endpoint)
            )
        """)

    def downgrade(self, session):
        session.execute("""
            DELETE FROM ua_target_resources
            WHERE kind = 'link' AND master = '30a76b42-f59d-465a-9f96-29a951377b22'
            """)
        session.execute("DROP TABLE em_endpoint_uses")
        session.execute("DROP TABLE em_endpoint_addresses")
        session.execute("ALTER TABLE em_services DROP COLUMN endpoints")


migration_step = MigrationStep()
