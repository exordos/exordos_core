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

from unittest.mock import MagicMock
from unittest.mock import patch
import uuid as sys_uuid

from gcl_sdk.agents.universal.drivers import pool
import pytest
from restalchemy.storage import exceptions as storage_exc

from exordos_core.storage.dm import models
from exordos_core.user_api.storage.api import controllers


def _spec(port=7776, core="core"):
    return {
        "kind": "rawstor",
        "endpoint": f"mds://{core}:{port}/",
    }


def _cluster(spec):
    return models.StorageCluster(
        uuid=sys_uuid.uuid4(),
        name="storage",
        driver_spec=pool.RawstorStorageClusterDriverSpec(
            **{k: v for k, v in spec.items() if k != "kind"}
        ),
    )


def _validate(spec, clusters, exclude_uuid=None):
    controller = controllers.StorageClustersController.__new__(
        controllers.StorageClustersController
    )
    with patch.object(
        models.StorageCluster,
        "objects",
        MagicMock(get_all=MagicMock(return_value=clusters)),
    ):
        controller._validate_driver_spec_uniqueness({"driver_spec": spec}, exclude_uuid)


def test_rejects_duplicate_mds_port_even_with_another_core_hostname():
    with pytest.raises(storage_exc.ConflictRecords):
        _validate(_spec(core="10.20.0.2"), [_cluster(_spec())])


def test_accepts_distinct_mds_ports():
    _validate(_spec(7778), [_cluster(_spec())])


def test_update_excludes_its_own_endpoints():
    cluster = _cluster(_spec())
    _validate(_spec(), [cluster], cluster.uuid)


def test_update_cannot_move_existing_disks_to_another_mds():
    cluster = _cluster(_spec())
    with pytest.raises(storage_exc.ConflictRecords):
        _validate(_spec(7778), [cluster], cluster.uuid)


@pytest.mark.parametrize(
    "field,value",
    [
        ("endpoint", "ost://core:7776"),
        ("endpoint", "mds://core/"),
        ("ost_endpoint", "file:///data"),
        ("endpoint", "mds://core:65536/"),
        ("ost_endpoint", "ost://host:not-a-port"),
    ],
)
def test_invalid_endpoint_returns_validation_error(field, value):
    spec = _spec()
    spec[field] = value
    with pytest.raises(controllers.InvalidRawstorEndpoint) as error:
        _validate(spec, [])
    assert error.value.code == 400


@pytest.mark.parametrize(
    "field,value",
    [
        ("ost_endpoint", "ost://host:7777"),
        ("location", "file:///data"),
        ("nodes", {}),
        ("pools", {}),
        ("managed", True),
        ("speed", "HOT"),
        ("ephemeral", False),
    ],
)
def test_cluster_rejects_node_and_pool_settings(field, value):
    spec = _spec()
    spec[field] = value
    with pytest.raises(
        controllers.InvalidRawstorEndpoint, match="configure nodes and pools separately"
    ):
        _validate(spec, [])
