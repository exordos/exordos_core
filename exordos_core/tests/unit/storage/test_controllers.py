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

from gcl_sdk.agents.universal.drivers import pool as ua_pool

from exordos_core.user_api.storage.api import controllers


class TestParseStoragePools:
    """`storage_pools` is a TypedList wrapping a KindModelSelectorType.

    Unlike a bare KindModelSelectorType field (e.g. driver_spec), the base
    controller's create() doesn't convert raw dicts inside a TypedList -
    passing them straight to the model raises a ParseError (each dict
    fails KindModelSelectorType.validate(), which expects an already
    -built model instance, not a dict). _parse_storage_pools is what
    makes `storages add --storage-pools '[...]'` work at all.
    """

    def test_converts_raw_dicts_into_thin_storage_pool_instances(self) -> None:
        raw = [
            {
                "kind": "thin_storage_pool",
                "pool_type": "rawstor",
                "name": "default",
                "speed": "HOT",
                "ephemeral": False,
                "capacity_usable": 100,
            }
        ]

        parsed = controllers.StorageClustersController._parse_storage_pools(None, raw)

        assert len(parsed) == 1
        pool = parsed[0]
        assert isinstance(pool, ua_pool.ThinStoragePool)
        assert pool.name == "default"
        assert pool.speed == "HOT"
        assert pool.ephemeral is False
        assert pool.capacity_usable == 100

    def test_passes_through_none_and_empty_list(self) -> None:
        assert (
            controllers.StorageClustersController._parse_storage_pools(None, None)
            is None
        )
        assert (
            controllers.StorageClustersController._parse_storage_pools(None, []) == []
        )
