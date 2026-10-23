#    Copyright 2026 Genesis Corporation.
#
#    All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.

from restalchemy.storage.sql import migrations

UPGRADE = [
    """
    ALTER TABLE public.nodes
        ADD COLUMN IF NOT EXISTS target_power_state character varying(16)
            DEFAULT 'ON' NOT NULL,
        ADD COLUMN IF NOT EXISTS power_state character varying(16)
            DEFAULT 'ON' NOT NULL
    """,
    """
    ALTER TABLE public.compute_sets
        ADD COLUMN IF NOT EXISTS target_power_state character varying(16)
            DEFAULT 'ON' NOT NULL
    """,
    """
    ALTER TABLE public.machines
        ADD COLUMN IF NOT EXISTS target_power_state character varying(16)
            DEFAULT 'ON' NOT NULL,
        ADD COLUMN IF NOT EXISTS power_state character varying(16)
            DEFAULT 'ON' NOT NULL
    """,
    # The view is the table of `NodeWithoutPorts` model, so it has to
    # have all columns of the `Node` model
    """
    CREATE OR REPLACE VIEW public.compute_nodes_without_ports AS
    SELECT
        nodes.uuid,
        nodes.project_id,
        nodes.name,
        nodes.description,
        nodes.cores,
        nodes.ram,
        nodes.node_type,
        nodes.status,
        nodes.created_at,
        nodes.updated_at,
        nodes.default_network,
        nodes.node_set,
        nodes.placement_policies,
        nodes.disk_spec,
        nodes.hostname,
        nodes.target_power_state,
        nodes.power_state
    FROM public.nodes
        LEFT JOIN public.compute_ports ports ON nodes.uuid = ports.node
    WHERE ports.uuid IS NULL
""",
]

DOWNGRADE = [
    # A column can't be dropped from the view with `CREATE OR REPLACE`
    "DROP VIEW IF EXISTS public.compute_nodes_without_ports",
    """
    CREATE VIEW public.compute_nodes_without_ports AS
    SELECT
        nodes.uuid,
        nodes.project_id,
        nodes.name,
        nodes.description,
        nodes.cores,
        nodes.ram,
        nodes.node_type,
        nodes.status,
        nodes.created_at,
        nodes.updated_at,
        nodes.default_network,
        nodes.node_set,
        nodes.placement_policies,
        nodes.disk_spec,
        nodes.hostname
    FROM public.nodes
        LEFT JOIN public.compute_ports ports ON nodes.uuid = ports.node
    WHERE ports.uuid IS NULL
""",
    "ALTER TABLE public.machines DROP COLUMN IF EXISTS power_state",
    "ALTER TABLE public.machines DROP COLUMN IF EXISTS target_power_state",
    "ALTER TABLE public.compute_sets DROP COLUMN IF EXISTS target_power_state",
    "ALTER TABLE public.nodes DROP COLUMN IF EXISTS power_state",
    "ALTER TABLE public.nodes DROP COLUMN IF EXISTS target_power_state",
]


class MigrationStep(migrations.AbstarctMigrationStep):
    def __init__(self):
        self._depends = ["0009-add-managed-token-metadata-8c42f019.py"]

    @property
    def migration_id(self):
        return "8c74f270-1629-4af2-9e06-086f6f7e6c9f"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        for statement in UPGRADE:
            session.execute(statement)

    def downgrade(self, session):
        for statement in DOWNGRADE:
            session.execute(statement)


migration_step = MigrationStep()
