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
from gcl_sdk.infra import constants as ic

from exordos_core.storage.builders import cluster as cluster_builder
from exordos_core.storage.dm import models


def _cluster(**overrides):
    kwargs = dict(
        uuid=sys_uuid.uuid4(),
        name="cluster1",
        driver_spec=ua_pool.RawstorStorageClusterDriverSpec(
            location="file:///var/lib/rawstor",
            endpoint="ost://10.0.0.5:7777",
            speed=ic.DiskSpeed.HOT.value,
            ephemeral=False,
        ),
        status=ua_pool.MachinePoolStatus.DISABLED.value,
    )
    kwargs.update(overrides)
    return models.Cluster(**kwargs)


class TestStorageClusterBuilderService:
    def test_prepare_iteration_filters_by_this_builder(self):
        builder_uuid = sys_uuid.uuid4()
        service = cluster_builder.StorageClusterBuilderService.__new__(
            cluster_builder.StorageClusterBuilderService
        )
        service._service_spec = type("Spec", (), {"uuid": builder_uuid})()

        context = service.prepare_iteration()

        assert context == {"clause_filters": {"builder": builder_uuid}}

    def test_actualize_outdated_instance_syncs_capacity_and_status(self):
        service = cluster_builder.StorageClusterBuilderService.__new__(
            cluster_builder.StorageClusterBuilderService
        )

        pool = ua_pool.ThinStoragePool(
            name="default", pool_type="rawstor", capacity_usable=50
        )
        current = _cluster(status=ua_pool.MachinePoolStatus.DISABLED.value)
        actual = _cluster(
            status=ua_pool.MachinePoolStatus.ACTIVE.value,
            storage_pools=[pool],
        )

        service.actualize_outdated_instance(current, actual)

        assert current.status == ua_pool.MachinePoolStatus.ACTIVE.value
        assert current.storage_pools == [pool]
