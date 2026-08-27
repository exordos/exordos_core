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
import uuid as sys_uuid

from gcl_sdk.agents.universal.drivers import pool as ua_pool

from exordos_core.boot_api.dm import models
from exordos_core.common import constants as c


def _row():
    uuid = sys_uuid.uuid4()
    return {
        "uuid": uuid,
        "firmware_uuid": uuid,
        "name": "machine",
        "description": "",
        "cores": 1,
        "ram": 1024,
        "boot": ua_pool.BootAlternative.network.value,
        "status": "ACTIVE",
        "project_id": c.ZERO_UUID,
        "machine_type": "VM",
        "image": None,
        "node": None,
        "pool": None,
    }


class TestMachineNetbootRestore:
    def test_a_row_read_one_at_a_time_carries_netboot_params(self):
        netboot = models.MachineNetboot.restore_from_storage(**_row())

        assert netboot.gc_host == models.LOCAL_GC_HOST
        assert netboot.kernel.startswith("tftp://")

    def test_a_row_read_as_part_of_a_page_carries_them_too(self):
        # A collection does not go through `restore_from_storage`: both
        # reads meet the model at `restore_row`, and before RESTAlchemy 16
        # the page left these unset.
        netboot = models.MachineNetboot.restore_row(_row())

        assert netboot.gc_host == models.LOCAL_GC_HOST
        assert netboot.gc_boot_api == models.LOCAL_GC_BOOT_API
        assert netboot.kernel.startswith("tftp://")
        assert netboot.initrd.startswith("tftp://")
