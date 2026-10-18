# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0.
"""Preserve the rendered SDN request when target projection changes value."""

import datetime
import uuid
from unittest import mock

from gcl_sdk.agents.universal import utils
from gcl_sdk.agents.universal.dm import models as ua

from exordos_core.elements.dm import models as em


def test_actualize_restores_create_input_with_unchanged_hash():
    desired = {
        "uuid": str(uuid.uuid4()),
        "name": "node",
        "default_network": {"subnet": str(uuid.uuid4())},
    }
    element = em.Element(name="core", version="1.0.0", link="$core")
    resource = em.Resource(
        uuid=uuid.UUID(desired["uuid"]),
        element=element,
        name="node",
        resource_link_prefix="$core.compute.nodes",
        value=desired,
    )
    target = ua.TargetResource(
        uuid=resource.uuid,
        kind="em_core_compute_nodes",
        value={
            key: value for key, value in desired.items() if key != "default_network"
        },
        hash=utils.calculate_hash(desired),
        full_hash="current-full-hash",
        tracked_at=resource.updated_at - datetime.timedelta(seconds=1),
    )
    resource.target_resource = target
    with (
        mock.patch.object(em.Resource, "render_target_state", return_value=desired),
        mock.patch.object(em.Resource, "_find_actual_resource", return_value=None),
        mock.patch.object(
            em.Resource, "calculate_full_hash", return_value="current-full-hash"
        ),
        mock.patch.object(em.Resource, "update"),
        mock.patch.object(ua.TargetResource, "update", autospec=True) as update,
    ):
        resource.actualize()

    update.assert_called_once_with(target)
    assert target.value == desired
    assert target.hash == utils.calculate_hash(desired)
    assert target.full_hash == "current-full-hash"
    assert target.tracked_at == resource.updated_at
