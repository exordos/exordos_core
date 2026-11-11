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
from unittest.mock import MagicMock
from unittest.mock import patch
from urllib.parse import urlparse
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


def _validate(spec, clusters, exclude_uuid=None, resources=()):
    controller = controllers.StorageClustersController.__new__(
        controllers.StorageClustersController
    )
    with (
        patch.object(
            controllers.ua_models.TargetResource,
            "objects",
            MagicMock(get_all=MagicMock(return_value=resources)),
        ),
        patch.object(
            controllers.ua_models.Resource,
            "objects",
            MagicMock(get_all=MagicMock(return_value=[])),
        ),
        patch.object(
            models.StorageCluster,
            "objects",
            MagicMock(get_all=MagicMock(return_value=clusters)),
        ),
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
        ("tier", "HOT"),
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


@pytest.mark.parametrize(
    "weight", [0, 1.5, float("inf"), float("nan"), None, "100", True, False, 1 << 64]
)
def test_node_rejects_weights_that_cannot_be_parsed_by_mds(weight):
    controller = controllers.StorageNodesController.__new__(
        controllers.StorageNodesController
    )
    with pytest.raises(controllers.InvalidRawstorEndpoint, match="positive integer"):
        controller.validate_member(
            {
                "agent": None,
                "driver_spec": {
                    "kind": "rawstor",
                    "endpoint": "ost://host:7777",
                    "location": "",
                    "weight": weight,
                },
            },
            _cluster(_spec()),
            existing=SimpleNamespace(
                agent=None, driver_spec=SimpleNamespace(location="")
            ),
        )


@pytest.mark.parametrize(
    "resource_type",
    [controllers.ua_models.TargetResource, controllers.ua_models.Resource],
)
def test_pool_removal_rejects_pending_and_actual_disks(resource_type):
    cluster = _cluster(_spec())
    pool_uuid = sys_uuid.uuid4()
    with (
        patch.object(
            controllers.compute_models.MachineVolume,
            "objects",
            MagicMock(get_all=MagicMock(return_value=[])),
        ),
        patch.object(
            controllers.ua_models.TargetResource,
            "objects",
            MagicMock(get_all=MagicMock(return_value=[])),
        ),
        patch.object(
            controllers.ua_models.Resource,
            "objects",
            MagicMock(get_all=MagicMock(return_value=[])),
        ),
    ):
        resource_type.objects.get_all.return_value = [
            SimpleNamespace(value={"storage_pool": str(pool_uuid)})
        ]
        with pytest.raises(storage_exc.ConflictRecords):
            controllers._require_no_disks(cluster, pool_uuid)


def test_node_endpoint_update_preserves_explicit_bind_address():
    controller = controllers.StorageNodesController.__new__(
        controllers.StorageNodesController
    )
    previous = SimpleNamespace(
        agent=sys_uuid.uuid4(),
        driver_spec=SimpleNamespace(
            endpoint="ost://host:7777",
            bind_address="127.0.0.1:7777",
            location="file:///data/ost",
        ),
    )
    with (
        patch.object(controller, "get", return_value=previous),
        patch.object(controllers.ClusterMemberController, "update") as update,
    ):
        controller.update(
            sys_uuid.uuid4(), driver_spec={"endpoint": "ost://HOST:7777/"}
        )
    assert update.call_args.kwargs["driver_spec"] == {"endpoint": "ost://host:7777"}
    assert "status" not in update.call_args.kwargs


def test_in_use_pool_policy_cannot_be_changed():
    existing = models.StoragePool(
        name="persistent",
        cluster=sys_uuid.uuid4(),
        driver_spec=controllers.ua_pool.rawstor_specs.RawstorPoolDriverSpec(),
    )
    data = existing.dump_to_simple_view()
    data["driver_spec"]["mirrors"] = 3
    controller = controllers.StoragePoolsController.__new__(
        controllers.StoragePoolsController
    )
    with patch.object(
        controllers,
        "_require_no_disks",
        side_effect=storage_exc.ConflictRecords(model="StoragePool", msg="in use"),
    ) as guard:
        with pytest.raises(storage_exc.ConflictRecords):
            controller.validate_member(data, _cluster(_spec()), existing)
    assert guard.call_args.args[1] == existing.uuid


def test_removed_cluster_port_remains_reserved_until_resources_disappear():
    resource = SimpleNamespace(uuid=sys_uuid.uuid4(), value={"driver_spec": _spec()})
    with pytest.raises(storage_exc.ConflictRecords):
        _validate(_spec(), [], resources=[resource])
    _validate(_spec(7778), [], resources=[resource])
    _validate(_spec(), [], exclude_uuid=resource.uuid, resources=[resource])


@pytest.mark.parametrize(
    "endpoint,expected",
    [
        ("ost://10.0.0.1:7777", "10.0.0.1:7777"),
        ("ost://ost.example:7777", "ost.example:7777"),
        ("ost://[::1]:7777", "[::1]:7777"),
    ],
)
def test_default_bind_uses_advertised_ip(endpoint, expected):
    assert controllers._default_bind_address(urlparse(endpoint)) == expected


@pytest.mark.parametrize("endpoint", ["ost://0.0.0.0:7777", "ost://[::]:7777"])
def test_non_specific_endpoint_requires_explicit_bind(endpoint):
    with pytest.raises(controllers.InvalidRawstorEndpoint):
        controllers._default_bind_address(urlparse(endpoint))


@pytest.mark.parametrize(
    "controller_type,policy",
    [
        (controllers.StorageNodesController, "storage_node"),
        (controllers.StoragePoolsController, "storage_pool"),
    ],
)
def test_member_permissions_use_their_own_policy(controller_type, policy):
    controller = controller_type.__new__(controller_type)
    controller._enforcer = MagicMock()
    controller._enforce("update")
    controller._enforcer.enforce.assert_called_once()
    call = controller._enforcer.enforce.call_args
    assert str(call.args[0]) == f"storage.{policy}.update"
    assert call.kwargs == {"do_raise": True}


@pytest.mark.parametrize("scheme", ["mds", "ost"])
@pytest.mark.parametrize("userinfo", [":secret@", "@", "user@", "user:secret@"])
def test_endpoint_rejects_all_userinfo(scheme, userinfo):
    with pytest.raises(controllers.InvalidRawstorEndpoint):
        controllers._validate_endpoint(f"{scheme}://{userinfo}host:7777", scheme)


@pytest.mark.parametrize("agent", [None, sys_uuid.uuid4()])
def test_endpoint_update_only_reconciles_managed_nodes(agent):
    controller = controllers.StorageNodesController.__new__(
        controllers.StorageNodesController
    )
    previous = SimpleNamespace(
        agent=agent,
        driver_spec=SimpleNamespace(
            endpoint="ost://old:7777", bind_address="old:7777", location="file:///data"
        ),
    )
    with (
        patch.object(controller, "get", return_value=previous),
        patch.object(controllers.ClusterMemberController, "update") as update,
    ):
        controller.update(sys_uuid.uuid4(), driver_spec={"endpoint": "ost://new:7777"})
    if agent is None:
        assert "status" not in update.call_args.kwargs
    else:
        assert update.call_args.kwargs["status"] == "IN_PROGRESS"
