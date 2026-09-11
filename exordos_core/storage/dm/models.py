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

import typing as tp
import uuid as sys_uuid

from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.agents.universal.drivers import pool as ua_pool
from restalchemy.dm import filters as dm_filters
from restalchemy.dm import models as ra_models
from restalchemy.dm import properties
from restalchemy.dm import types
from restalchemy.dm import types_dynamic
from restalchemy.storage.sql import orm

from exordos_core.compute.pool.dm import models as pool_models


class StorageCluster(
    ra_models.ModelWithUUID,
    ra_models.ModelWithNameDesc,
    ra_models.ModelWithTimestamp,
    orm.SQLStorableMixin,
    ra_models.SimpleViewMixin,
):
    """A storage cluster - a dissagregated storage backend disks can be
    scheduled onto in addition to a hypervisor's own local pools.

    Deliberately not a subtype of MachinePool: the "coordinator resource"
    wiring it needs (InstanceMixin, get_resource_kind/get_filter_clause/
    get_resource_target_fields, generic UA builder dispatch) has no
    VM-specific code in it - the VM-specificity lives entirely in
    MachinePool's own fields (machine_type, cores_ratio/ram_ratio), none
    of which a storage cluster needs.
    """

    __tablename__ = "storage_clusters"

    driver_spec = properties.property(
        types_dynamic.KindModelSelectorType(
            types_dynamic.KindModelType(ua_pool.RawstorStorageClusterDriverSpec),
        ),
        required=True,
    )
    agent = properties.property(types.AllowNone(types.UUID()), default=None)
    builder = properties.property(types.AllowNone(types.UUID()), default=None)
    status = properties.property(
        types.Enum([s.value for s in ua_pool.MachinePoolStatus]),
        default=ua_pool.MachinePoolStatus.DISABLED.value,
    )
    storage_pools = properties.property(
        types.TypedList(
            types_dynamic.KindModelSelectorType(
                types_dynamic.KindModelType(ua_pool.ThinStoragePool),
            ),
        ),
        default=list,
    )


class Cluster(
    StorageCluster,
    ua_models.InstanceMixin,
    pool_models.SchedulableToAgentFromAgentFieldMixin,
):
    @classmethod
    def get_resource_kind(cls) -> str:
        return "storage_cluster"

    @classmethod
    def get_filter_clause(
        cls, builder: sys_uuid.UUID
    ) -> tp.Optional[tp.Dict[str, dm_filters.AbstractClause]]:
        """Get filter clause for the instance model.

        The clause is returned back to the service to take a chance for
        the service enrich the clause. After that the clause is used in
        the database queries. The service haven't must call method
        `get_new_instances` and other with the clause if it was returned.
        It depends on the service implementation.
        """
        return {"builder": dm_filters.EQ(str(builder))}

    def get_resource_target_fields(self) -> tp.Collection[str]:
        """Return the collection of target fields.

        Refer to the Resource model for more details about target fields.
        """
        return frozenset(("uuid", "driver_spec"))
