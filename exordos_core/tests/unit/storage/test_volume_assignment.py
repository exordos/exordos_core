#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License")

from unittest.mock import MagicMock
import uuid

from gcl_sdk.agents.universal.drivers import pool as sdk_pool

from exordos_core.compute.pool.dm import models as compute
from exordos_core.storage.dm import models as storage


def test_volume_agent_payload_resolves_current_pool_and_cluster(monkeypatch):
    cluster = storage.StorageCluster(
        name="cluster",
        driver_spec=sdk_pool.RawstorStorageClusterDriverSpec(
            endpoint="mds://core:7776/"
        ),
    )
    pool = storage.StoragePool(name="persistent", cluster=cluster.uuid)
    monkeypatch.setattr(
        storage.StoragePool, "objects", MagicMock(get_one=MagicMock(return_value=pool))
    )
    monkeypatch.setattr(
        storage.StorageCluster,
        "objects",
        MagicMock(get_one=MagicMock(return_value=cluster)),
    )
    volume = compute.MachineVolume(
        project_id=uuid.uuid4(), name="v", size=10, storage_pool=str(pool.uuid)
    )
    first = volume.to_ua_resource()
    assert first.value["storage_location"] == "mds://core:7776/"
    assert first.value["storage_policy"]["mirrors"] == 2
    assert "storage_location" not in volume.dump_to_simple_view()
    assert "storage_policy" not in volume.dump_to_simple_view()

    pool.name = "renamed"
    pool.mirrors = 3
    pool.chunk_size = 2 << 30
    pool.failure_domain = "rack"
    cluster.driver_spec.endpoint = "mds://other-core:7778/"
    second = volume.to_ua_resource()
    assert second.value["storage_pool"] == str(pool.uuid)
    assert second.value["storage_location"] == "mds://other-core:7778/"
    assert second.value["storage_policy"] == {
        "pool_uuid": str(pool.uuid),
        "mirrors": 3,
        "chunk_size": 2 << 30,
        "failure_domain": "rack",
    }
    assert first.hash != second.hash


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
