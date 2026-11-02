#    Copyright 2026 Genesis Corporation.
#
#    All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import json

from gcl_sdk.agents.universal import utils
from restalchemy.storage.sql import migrations


def _rewrite(session, set_power_state):
    pairs = session.execute(
        """
        SELECT t.res_uuid, t.value::text AS target_value,
            t.status AS target_status, a.value::text AS actual_value,
            a.status AS actual_status, m.target_power_state, m.power_state
        FROM ua_target_resources t
            JOIN machines m ON m.uuid = t.uuid
            LEFT JOIN ua_actual_resources a ON a.res_uuid = t.res_uuid
        WHERE t.kind = 'pool_machine';
        """
    ).fetchall()
    for pair in pairs:
        res_uuid = pair["res_uuid"]
        target_status = pair["target_status"]
        actual_status = pair["actual_status"]

        target = json.loads(pair["target_value"])
        set_power_state(target, pair["target_power_state"])
        # The target value consists of the target fields only
        target_hash = utils.calculate_hash(target)

        if pair["actual_value"] is not None:
            actual = json.loads(pair["actual_value"])
            set_power_state(actual, pair["power_state"])
            # The same way the agent does: the hash is calculated over
            # the actual value projected onto the shape of the target one
            actual_hash = utils.calculate_hash(
                utils.project_onto(actual, utils.value_shape(target))
            )
            session.execute(
                "UPDATE ua_actual_resources "
                "SET value = %s::jsonb, hash = %s, full_hash = %s "
                "WHERE res_uuid = %s;",
                (
                    json.dumps(actual),
                    actual_hash,
                    utils.calculate_hash(actual),
                    res_uuid,
                ),
            )
            # The agent doesn't report the unchanged actual resource, so
            # set the status as the builder does on the agent report.
            if actual_status == "ACTIVE" and actual_hash == target_hash:
                target_status = actual_status

        # The target full hash is replaced with the actual one by the
        # builder, so it's left as is.
        session.execute(
            "UPDATE ua_target_resources "
            "SET value = %s::jsonb, hash = %s, status = %s "
            "WHERE res_uuid = %s;",
            (json.dumps(target), target_hash, target_status, res_uuid),
        )


def _set_power_state(value, power_state):
    # Keep the power state if it's already set, the actual one is
    # reported by the agent.
    value["power_state"] = value.get("power_state") or power_state


def _unset_power_state(value, power_state):
    value.pop("power_state", None)


class MigrationStep(migrations.AbstractMigrationStep):
    def __init__(self):
        self._depends = ["0010-node-power-state-8c74f270.py"]

    @property
    def migration_id(self):
        return "f1ad94f8-80b5-4998-8217-e674e7c887f3"

    @property
    def is_manual(self):
        return False

    def upgrade(self, session):
        # The power state feature added "power_state" to the pool machine
        # target fields while the resources of the already provisioned
        # machines don't have the key. On outdate the builder rebuilds such
        # a target from the stored value with "power_state": null, the
        # agent reports "ON", so the target never converges and the pool
        # driver logs "Unknown update action for machine" every cycle.
        #
        # Set the power state from the machines table (the migration 0010
        # defaulted it to 'ON') and recalculate the hashes the same way the
        # agent does, so the agent finds nothing to update.
        _rewrite(session, _set_power_state)

    def downgrade(self, session):
        # The previous agent calculates the hashes without the power state
        _rewrite(session, _unset_power_state)


migration_step = MigrationStep()
