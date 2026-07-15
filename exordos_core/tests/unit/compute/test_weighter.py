#    Copyright 2025 Genesis Corporation.
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

import pytest

from exordos_core.compute.dm import models
from exordos_core.compute.scheduler.driver import base
from exordos_core.compute.scheduler.driver.weighter import relative

_DEFAULT_DRIVER_SPEC = models.LibvirtPoolDriverSpec(
    connection_uri="qemu+tcp://127.0.0.1/system",
)


class TestSchedulerWeighter:
    @pytest.fixture
    def weighter(self):
        return relative.RelativeCoreRamWeighter()

    @pytest.fixture
    def pools(self):
        return [
            # 50% used
            base.MachinePoolBundle(
                pool=models.MachinePool(
                    driver_spec=_DEFAULT_DRIVER_SPEC,
                    all_cores=100,
                    avail_cores=50,
                    all_ram=100000,
                    avail_ram=50000,
                ),
                volumes=[],
            ),
            # 20% used
            base.MachinePoolBundle(
                pool=models.MachinePool(
                    driver_spec=_DEFAULT_DRIVER_SPEC,
                    all_cores=100,
                    avail_cores=80,
                    all_ram=100000,
                    avail_ram=80000,
                ),
                volumes=[],
            ),
            # Fully used
            base.MachinePoolBundle(
                pool=models.MachinePool(
                    driver_spec=_DEFAULT_DRIVER_SPEC,
                    all_cores=100,
                    avail_cores=0,
                    all_ram=100000,
                    avail_ram=0,
                ),
                volumes=[],
            ),
        ]

    def test_weight_empty_system(self, weighter):
        """Test weighting when the system is empty (no usage)."""
        empty_pools = [
            base.MachinePoolBundle(
                pool=models.MachinePool(
                    driver_spec=_DEFAULT_DRIVER_SPEC,
                    all_cores=100,
                    avail_cores=100,
                    all_ram=100000,
                    avail_ram=100000,
                ),
                volumes=[],
            )
        ]
        weights = list(weighter.weight(empty_pools))
        assert len(weights) == 1
        assert weights[0] == 1.0

    def test_weight(self, weighter, pools):
        """Test the weight calculation with different levels of usage."""
        weights = list(weighter.weight(pools))
        assert len(weights) == 3
        # Expect pools with less usage to have a higher weight
        assert weights[1] > weights[0] > weights[2]

    def test_weight_with_overused_pool(self, weighter):
        """Test weighting with a pool that is overused."""
        overused_pools = [
            base.MachinePoolBundle(
                pool=models.MachinePool(
                    driver_spec=_DEFAULT_DRIVER_SPEC,
                    all_cores=100,
                    avail_cores=-10,
                    all_ram=100000,
                    avail_ram=-5000,
                ),
                volumes=[],
            )
        ]
        weights = list(weighter.weight(overused_pools))
        assert len(weights) == 1

        # Weight for overused pool might be treated as the worst case
        assert weights[0] == 0.0
