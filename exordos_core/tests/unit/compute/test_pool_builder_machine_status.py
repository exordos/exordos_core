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

from types import SimpleNamespace
from unittest import mock
import uuid as sys_uuid

from gcl_sdk.agents.universal.drivers import pool as ua_pool
from gcl_sdk.infra import constants as ic

from exordos_core.compute.builders import pool as pool_builder
from exordos_core.compute.pool.dm import models as pool_models

MS = ua_pool.MachineStatus
PS = ic.PowerState


def _pool_machine(uuid, project_id, pool, **kwargs):
    params = dict(
        uuid=uuid,
        name="node",
        project_id=project_id,
        cores=1,
        ram=512,
        image="image",
        boot=ua_pool.BootAlternative.hd0.value,
        pool=pool,
        power_state=PS.ON.value,
        status=MS.ACTIVE.value,
    )
    params.update(kwargs)
    return pool_models.PoolMachine(**params)


def _guest_machine(uuid, **kwargs):
    params = dict(
        uuid=uuid,
        image="image",
        hostname="node",
        boot=ua_pool.BootAlternative.hd0.value,
        status=MS.ACTIVE.value,
    )
    params.update(kwargs)
    return pool_models.GuestMachine(**params)


class TestPoolBuilderUpdateMachineStatus:
    def setup_method(self):
        self.uuid = sys_uuid.uuid4()
        self.project_id = sys_uuid.uuid4()
        self.pool = sys_uuid.uuid4()
        # The machine is marked `IN_PROGRESS` by the node builder on update
        self.machine = SimpleNamespace(
            uuid=self.uuid,
            image="image",
            target_power_state=PS.ON.value,
            power_state=PS.ON.value,
            status=MS.IN_PROGRESS.value,
        )

    def _pool(self, **kwargs):
        return _pool_machine(self.uuid, self.project_id, self.pool, **kwargs)

    def _guest(self, **kwargs):
        return _guest_machine(self.uuid, **kwargs)

    def _update(self, new_derivatives, derivative_pairs):
        service = pool_builder.PoolBuilderService(sys_uuid.uuid4(), mock.Mock())
        with mock.patch.object(
            service,
            "_actualize_machine_derivatives_on_create_update",
            return_value=new_derivatives,
        ):
            result = service.update_instance_derivatives(
                self.machine, mock.Mock(), derivative_pairs
            )

        assert result == new_derivatives

    def test_active_when_derivatives_unchanged(self):
        self._update(
            (self._pool(), self._guest()),
            ((self._pool(), self._pool()), (self._guest(), self._guest())),
        )

        assert self.machine.status == MS.ACTIVE.value

    def test_in_progress_when_derivatives_changed(self):
        self._update(
            (self._pool(cores=2), self._guest()),
            ((self._pool(), self._pool()), (self._guest(), self._guest())),
        )

        assert self.machine.status == MS.IN_PROGRESS.value

    def test_in_progress_when_agent_not_reached_target(self):
        # The previous change hasn't been applied by the agent yet
        self._update(
            (self._pool(cores=2), self._guest()),
            (
                (self._pool(cores=2), self._pool()),
                (self._guest(), self._guest()),
            ),
        )

        assert self.machine.status == MS.IN_PROGRESS.value

    def test_in_progress_when_no_actual_derivative(self):
        self._update(
            (self._pool(), self._guest()),
            ((self._pool(), self._pool()), (self._guest(), None)),
        )

        assert self.machine.status == MS.IN_PROGRESS.value

    def test_actual_status_taken_when_derivatives_unchanged(self):
        self._update(
            (self._pool(), self._guest()),
            (
                (self._pool(), self._pool(status=MS.ERROR.value)),
                (self._guest(), self._guest()),
            ),
        )

        assert self.machine.status == MS.ERROR.value
