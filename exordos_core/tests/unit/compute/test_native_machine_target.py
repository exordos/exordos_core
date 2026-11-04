# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License").

import uuid

from gcl_sdk.agents.universal.drivers.pool import MetaMachine
import pytest

from exordos_core.compute.pool.dm.models import PoolMachine


@pytest.mark.parametrize("user_data", [None, "#cloud-config\nhostname: cloud\n"])
def test_machine_target_only_sends_user_data_when_present(user_data):
    machine = PoolMachine(
        uuid=uuid.uuid4(),
        pool=uuid.uuid4(),
        project_id=uuid.uuid4(),
        cores=1,
        ram=512,
        user_data=user_data,
    )
    target = machine.to_ua_resource()
    if user_data is None:
        assert "user_data" not in target.value
    else:
        assert target.value["user_data"] == user_data
    actual = MetaMachine.from_ua_resource(target).to_ua_resource("pool_machine")
    assert actual.hash == target.hash
