#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License").

from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest

from exordos_core.storage import volumes


@pytest.fixture
def inventory(monkeypatch):
    cluster = SimpleNamespace(
        uuid=uuid.uuid4(),
        agent=uuid.uuid4(),
        driver_spec=SimpleNamespace(endpoint="mds://core:7776/"),
    )
    policy = SimpleNamespace(
        uuid=uuid.uuid4(),
        cluster=cluster.uuid,
        driver_spec=SimpleNamespace(mirrors=2, failure_domain="server"),
    )
    volume = SimpleNamespace(
        uuid=uuid.uuid4(),
        pool=uuid.uuid4(),
        project_id=uuid.uuid4(),
        size=8,
        storage_pool=str(policy.uuid),
    )
    data = SimpleNamespace(
        cluster=cluster,
        policy=policy,
        volume=volume,
        desired=[volume],
        jobs={},
        attachments=[],
        actual=[],
    )
    monkeypatch.setattr(
        volumes.models.StorageCluster,
        "objects",
        Mock(get_all=Mock(return_value=[cluster])),
    )
    monkeypatch.setattr(
        volumes.models.StoragePool, "objects", Mock(get_all=Mock(return_value=[policy]))
    )
    monkeypatch.setattr(
        volumes.compute.MachineVolume,
        "objects",
        Mock(get_all=Mock(side_effect=lambda **kwargs: data.desired)),
    )
    monkeypatch.setattr(
        volumes.ua.TargetResource,
        "objects",
        Mock(
            get_all=Mock(
                side_effect=lambda **kwargs: (
                    list(data.jobs.values())
                    if kwargs["filters"]["kind"].value == "storage_volume"
                    else data.attachments
                )
            )
        ),
    )
    monkeypatch.setattr(
        volumes.ua.Resource,
        "objects",
        Mock(get_all=Mock(side_effect=lambda **kwargs: data.actual)),
    )
    monkeypatch.setattr(
        volumes.ua.TargetResource,
        "insert",
        lambda self: data.jobs.update({self.uuid: self}),
    )
    monkeypatch.setattr(volumes.ua.TargetResource, "update", lambda self: None)
    monkeypatch.setattr(
        volumes.ua.TargetResource, "delete", lambda self: data.jobs.pop(self.uuid)
    )
    return data


def test_object_job_is_assigned_to_core_not_hypervisor(inventory):
    data = inventory
    volumes.sync_targets(uuid.uuid4())
    job = data.jobs[data.volume.uuid]
    assert job.agent == data.cluster.agent
    assert job.value["project_id"] == str(data.volume.project_id)
    assert job.value["storage_policy"] == {"mirrors": 2, "failure_domain": "server"}
    assert "rawstor_tls" not in job.value
    before = job.hash
    data.volume.size = 16
    volumes.sync_targets(uuid.uuid4())
    assert job.value["size"] == 16 and job.hash != before


@pytest.mark.parametrize("attachment", ["target", "actual"])
def test_object_is_retained_until_hypervisor_confirms_detach(inventory, attachment):
    data = inventory
    volumes.sync_targets(uuid.uuid4())
    data.desired = []
    references = data.attachments if attachment == "target" else data.actual
    references.append(SimpleNamespace(uuid=data.volume.uuid))
    volumes.sync_targets(uuid.uuid4())
    assert data.volume.uuid in data.jobs
    references.clear()
    volumes.sync_targets(uuid.uuid4())
    assert not data.jobs


def test_missing_core_assignment_keeps_existing_object_job(inventory):
    data = inventory
    volumes.sync_targets(uuid.uuid4())
    job = data.jobs[data.volume.uuid]
    data.cluster.agent = None
    volumes.sync_targets(uuid.uuid4())
    assert data.jobs[data.volume.uuid] is job


def test_other_builders_object_jobs_are_untouched(inventory):
    data = inventory
    foreign = volumes.ua.TargetResource(
        uuid=uuid.uuid4(), kind="storage_volume", value={"cluster": str(uuid.uuid4())}
    )
    data.jobs[foreign.uuid] = foreign
    data.desired = []
    volumes.sync_targets(uuid.uuid4())
    assert data.jobs == {foreign.uuid: foreign}
