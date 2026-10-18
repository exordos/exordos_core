# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0.

import collections
import json
from unittest import mock
import uuid

import netaddr
import pytest

from exordos_core.compute.dm import models
from exordos_core.network.driver.flat import DHCPContext


@pytest.mark.parametrize(
    "legacy", [{"floating_ips": []}, {"config": {"kind": "simple", "dhcp": None}}]
)
def test_old_context_is_rebuilt_from_current_database(tmp_path, legacy):
    path = tmp_path / "context.json"
    subnet = uuid.uuid4()
    path.write_text(
        json.dumps(
            {
                "cfg_hash": "old",
                "subnets": [],
                "port_map": {
                    str(subnet): [
                        {
                            "uuid": str(uuid.uuid4()),
                            "project_id": str(uuid.uuid4()),
                            "subnet": str(subnet),
                            **legacy,
                        }
                    ]
                },
            }
        )
    )
    assert DHCPContext.load_ctx(str(path)).is_empty()


def test_context_round_trip_omits_derived_port_fields(tmp_path):
    path = tmp_path / "context.json"
    port = models.Port(project_id=uuid.uuid4(), subnet=uuid.uuid4())
    subnet = models.Subnet(
        uuid=port.subnet,
        project_id=port.project_id,
        network=uuid.uuid4(),
        cidr=netaddr.IPNetwork("10.101.0.0/24"),
    )
    context = DHCPContext(
        "current", [subnet], collections.defaultdict(list, {port.subnet: [port]})
    )
    with (
        mock.patch.object(models.Subnet, "objects") as subnets,
        mock.patch(
            "exordos_core.user_api.network.dm.models.PortSecurityGroup.objects"
        ) as security,
    ):
        context.save_ctx(str(path))
        subnets.get_one_or_none.assert_not_called()
        security.get_all.assert_not_called()
    restored = DHCPContext.load_ctx(str(path))
    assert restored.subnets[0].uuid == subnet.uuid
    assert restored.subnets[0].routers is None
    assert restored.subnets[0].dns_servers is None
    assert restored.cfg_hash == "current"
    assert restored.port_map[port.subnet][0].uuid == port.uuid
