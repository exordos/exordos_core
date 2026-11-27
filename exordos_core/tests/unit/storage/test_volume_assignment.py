#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License")

from unittest.mock import MagicMock
import uuid

from gcl_sdk.agents.universal.drivers import pool as sdk_pool
from gcl_sdk.agents.universal.drivers import rawstor_specs
import pytest

from exordos_core.compute.pool.dm import models as compute
from exordos_core.storage.dm import models as storage


def test_volume_agent_payload_resolves_current_pool_and_cluster(monkeypatch):
    cluster = storage.StorageCluster(
        name="cluster",
        driver_spec=sdk_pool.RawstorStorageClusterDriverSpec(
            endpoint="mds://core:7776/"
        ),
    )
    pool = storage.StoragePool(
        name="persistent",
        cluster=cluster.uuid,
        driver_spec=rawstor_specs.RawstorPoolDriverSpec(),
    )
    monkeypatch.setattr(
        storage.StoragePool,
        "objects",
        MagicMock(get_one=MagicMock(return_value=pool)),
    )
    monkeypatch.setattr(
        storage.StorageCluster,
        "objects",
        MagicMock(get_one=MagicMock(return_value=cluster)),
    )
    volume = compute.MachineVolume(
        project_id=uuid.uuid4(),
        name="v",
        size=10,
        storage_pool=str(pool.uuid),
        storage_backend="rawstor",
    )
    first = volume.to_ua_resource()
    assert first.value["storage_location"] == "mds://core:7776/"
    assert first.value["storage_policy"]["mirrors"] == 2
    assert "storage_location" not in volume.dump_to_simple_view()
    assert "storage_policy" not in volume.dump_to_simple_view()

    pool.name = "renamed"
    pool.driver_spec.mirrors = 3
    pool.driver_spec.failure_domain = "rack"
    cluster.driver_spec.endpoint = "mds://other-core:7778/"
    second = volume.to_ua_resource()
    assert second.value["storage_pool"] == str(pool.uuid)
    assert second.value["storage_location"] == "mds://other-core:7778/"
    assert second.value["storage_policy"] == {
        "pool_uuid": str(pool.uuid),
        "mirrors": 3,
        "failure_domain": "rack",
    }
    assert first.hash != second.hash

    monkeypatch.setattr(compute.features, "rawstor_enabled", lambda: False)
    with pytest.raises(ValueError, match="RawStor integration is disabled"):
        volume.to_ua_resource()


def test_local_volume_does_not_resolve_cluster(monkeypatch):
    objects = MagicMock()
    monkeypatch.setattr(storage.StoragePool, "objects", objects)
    volume = compute.MachineVolume(
        project_id=uuid.uuid4(), name="v", size=10, storage_pool="default"
    )
    resource = volume.to_ua_resource()
    assert resource.value["storage_location"] is None
    assert resource.value["storage_policy"] == {}
    objects.get_one.assert_not_called()


def test_missing_uuid_pool_falls_back_to_local_volume(monkeypatch):
    monkeypatch.setattr(
        storage.StoragePool, "objects", MagicMock(get_all=MagicMock(return_value=[]))
    )
    volume = compute.MachineVolume(
        project_id=uuid.uuid4(), name="v", size=10, storage_pool=str(uuid.uuid4())
    )
    assert volume.to_ua_resource().value["storage_location"] is None


def test_uuid_named_local_pool_wins_over_remote_registration(monkeypatch):
    identifier = uuid.uuid4()
    owner = compute.Pool(
        driver_spec=sdk_pool.ExordosLocalHyperDriverSpec(
            connection_uri="test:///default", node=uuid.uuid4()
        ),
        storage_pools=[sdk_pool.ThinStoragePool(name=str(identifier), pool_type="dir")],
    )
    monkeypatch.setattr(
        storage.StoragePool,
        "objects",
        MagicMock(
            get_all=MagicMock(
                side_effect=AssertionError("must not resolve a local pool as remote")
            )
        ),
    )
    volume = compute.MachineVolume(
        project_id=uuid.uuid4(),
        name="local",
        size=1,
        pool=owner,
        storage_pool=str(identifier),
    )
    assert volume.to_ua_resource().value["storage_location"] is None


def test_remote_backend_survives_new_local_uuid_collision(monkeypatch):
    cluster = storage.StorageCluster(
        name="cluster",
        driver_spec=sdk_pool.RawstorStorageClusterDriverSpec(
            endpoint="mds://core:7776/"
        ),
    )
    remote = storage.StoragePool(
        name="remote",
        cluster=cluster.uuid,
        driver_spec=rawstor_specs.RawstorPoolDriverSpec(),
    )
    owner = compute.Pool(
        driver_spec=sdk_pool.ExordosLocalHyperDriverSpec(
            connection_uri="test:///default", node=uuid.uuid4()
        )
    )
    monkeypatch.setattr(
        storage.StoragePool,
        "objects",
        MagicMock(get_one=MagicMock(return_value=remote)),
    )
    monkeypatch.setattr(
        storage.StorageCluster,
        "objects",
        MagicMock(get_one=MagicMock(return_value=cluster)),
    )
    volume = compute.MachineVolume(
        name="disk",
        project_id=uuid.uuid4(),
        size=8,
        pool=owner,
        storage_pool=str(remote.uuid),
        storage_backend="rawstor",
    )
    before = volume.to_ua_resource()
    owner.storage_pools = [
        sdk_pool.ThinStoragePool(name=str(remote.uuid), pool_type="dir")
    ]
    after = volume.to_ua_resource()
    assert (
        before.value["storage_location"]
        == after.value["storage_location"]
        == "mds://core:7776/"
    )
    assert before.value["storage_policy"] == after.value["storage_policy"]
    assert volume.dump_to_simple_view()["storage_backend"] == "rawstor"


def test_local_backend_remains_local_after_pool_disappears(monkeypatch):
    identifier = str(uuid.uuid4())
    objects = MagicMock(
        side_effect=AssertionError("must not resolve a local volume as remote")
    )
    monkeypatch.setattr(storage.StoragePool, "objects", objects)
    volume = compute.MachineVolume(
        name="local",
        project_id=uuid.uuid4(),
        size=1,
        storage_pool=identifier,
        storage_backend="local",
    )
    assert volume.to_ua_resource().value["storage_location"] is None
    objects.get_one.assert_not_called()
