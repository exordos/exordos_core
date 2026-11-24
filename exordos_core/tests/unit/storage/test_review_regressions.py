#    Copyright 2026 Genesis Corporation.
#    Licensed under the Apache License, Version 2.0 (the "License").

from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import patch
import uuid

from gcl_sdk.agents.universal.drivers import pool
import pytest
from restalchemy.storage import exceptions as storage_exc

from exordos_core.user_api.storage.api import controllers as api


@pytest.mark.parametrize(
    "host",
    [
        "bad%host",
        "bad_host",
        "-bad.example",
        "bad-.example",
        "host..example",
        "host.example..",
        "999.1.1.1",
        "127.1",
        "0177.0.0.1",
        "0x7f000001",
        "0x7f.1",
        "a" * 64 + ".example",
        ".".join(["a" * 63] * 4),
        "имя.example",
    ],
)
def test_malformed_hostname_is_rejected(host):
    with pytest.raises(api.InvalidRawstorEndpoint):
        api._validate_endpoint(f"mds://{host}:7776/", "mds")


@pytest.mark.parametrize(
    "host",
    [
        "host",
        "Host.Example.",
        "123.example",
        "xn--e1afmkfd.xn--p1ai",
        "a" * 63 + ".example",
        "10.100.0.2",
    ],
)
def test_valid_hostname_or_ipv4_is_accepted(host):
    api._validate_endpoint(f"ost://{host}:7777/", "ost")


@pytest.mark.parametrize(
    "agent", ["not-a-uuid", [], {}, True, 123, 10000000000000000000000000000000]
)
def test_bad_agent_is_rejected_before_registration_lookup(agent):
    controller = api.StorageNodesController.__new__(api.StorageNodesController)
    data = {
        "agent": agent,
        "driver_spec": {
            "kind": "rawstor",
            "endpoint": "ost://host:7777",
            "location": "file:///data/ost",
            "bind_address": "host:7777",
            "failure_domain_path": "host",
        },
    }
    with patch.object(api.ua_models.UniversalAgent, "objects") as registrations:
        with pytest.raises(api.InvalidRawstorEndpoint, match="valid UUID"):
            controller.validate_member(data, SimpleNamespace(uuid=uuid.uuid4()))
    registrations.get_one.assert_not_called()


@pytest.mark.parametrize("source", ["sql", "target", "actual"])
def test_uuid_named_local_pool_does_not_block_remote_pool_deletion(source):
    identifier, owner = uuid.uuid4(), uuid.uuid4()
    compute_pool = SimpleNamespace(
        storage_pools=[pool.ThinStoragePool(name=str(identifier), pool_type="dir")]
    )
    value = {
        "storage_pool": str(identifier),
        "pool": str(owner),
        "storage_location": None,
    }
    with (
        patch.object(
            api.compute_models.MachineVolume,
            "objects",
            MagicMock(
                get_all=MagicMock(
                    return_value=[
                        SimpleNamespace(storage_pool=str(identifier), pool=owner)
                    ]
                    if source == "sql"
                    else []
                )
            ),
        ),
        patch.object(
            api.compute_models.MachinePool,
            "objects",
            MagicMock(get_all=MagicMock(return_value=[compute_pool])),
        ) as owners,
        patch.object(
            api.ua_models.TargetResource,
            "objects",
            MagicMock(
                get_all=MagicMock(
                    return_value=[SimpleNamespace(value=value)]
                    if source == "target"
                    else []
                )
            ),
        ),
        patch.object(
            api.ua_models.Resource,
            "objects",
            MagicMock(
                get_all=MagicMock(
                    return_value=[SimpleNamespace(value=value)]
                    if source == "actual"
                    else []
                )
            ),
        ),
    ):
        api._require_no_disks(SimpleNamespace(uuid=uuid.uuid4()), identifier)
    owners.get_all.assert_called_once()


@pytest.mark.parametrize(
    "local_exists,location", [(False, None), (True, "mds://core:7776/")]
)
def test_remote_or_uncertain_resource_still_blocks_deletion(local_exists, location):
    identifier, owner = uuid.uuid4(), uuid.uuid4()
    compute_pool = SimpleNamespace(
        storage_pools=[pool.ThinStoragePool(name=str(identifier), pool_type="dir")]
    )
    with patch.object(
        api.compute_models.MachinePool,
        "objects",
        MagicMock(
            get_all=MagicMock(return_value=[compute_pool] if local_exists else [])
        ),
    ):
        assert api._references_remote_pool(
            {
                "pool": str(owner),
                "storage_pool": str(identifier),
                "storage_location": location,
            },
            [str(identifier)],
            {},
        )


@pytest.mark.parametrize("status", ["NEW", "IN_PROGRESS", "ERROR", "ACTIVE"])
@pytest.mark.parametrize("unreachable", [False, True])
def test_missing_report_cannot_bypass_ost_emptiness_check(status, unreachable):
    member = SimpleNamespace(
        uuid=uuid.uuid4(),
        agent=uuid.uuid4(),
        status=status,
        driver_spec=SimpleNamespace(endpoint="ost://host:7777"),
    )
    with patch.object(api, "_require_no_disks"), patch("rawstor.Location") as location:
        if unreachable:
            location.return_value.objects.side_effect = ConnectionError("offline")
        else:
            location.return_value.objects.return_value = iter([object()])
        with pytest.raises(storage_exc.ConflictRecords):
            api.StorageNodesController.validate_delete(member, SimpleNamespace())
    location.return_value.objects.assert_called_once()


def test_empty_reachable_ost_can_be_deleted_without_old_report():
    member = SimpleNamespace(
        agent=uuid.uuid4(),
        status="ERROR",
        driver_spec=SimpleNamespace(endpoint="ost://host:7777"),
    )
    with patch.object(api, "_require_no_disks"), patch("rawstor.Location") as location:
        location.return_value.objects.return_value = iter(())
        api.StorageNodesController.validate_delete(member, SimpleNamespace())
