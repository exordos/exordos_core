# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0.
"""An EM snapshot must not erase a concurrent scheduler assignment."""

import datetime
import uuid
from unittest import mock
from types import MethodType

import pytest
from gcl_sdk.agents.universal import utils
from gcl_sdk.agents.universal.dm import models as ua
from restalchemy.storage.sql import engines

from exordos_core.elements.dm import models as em


@pytest.mark.parametrize("outer_session", [True, False])
@pytest.mark.parametrize("changed", ["hash", "value", "full_hash", "tracked_at"])
def test_actualize_preserves_concurrent_agent_assignment(changed, outer_session):
    node = uuid.uuid4()
    agent = uuid.uuid4()
    element = em.Element(name="core", version="1.0.0", link="$core")
    desired = {"name": "variable", "uuid": str(uuid.uuid4())}
    resource = em.Resource(
        uuid=uuid.UUID(desired["uuid"]),
        element=element,
        name="variable",
        resource_link_prefix="$core.vs.variables",
        value=desired,
    )
    stale = ua.TargetResource(
        uuid=resource.uuid,
        kind="em_core_vs_variables",
        value=desired,
        hash=utils.calculate_hash(desired),
        full_hash="current-full-hash",
        tracked_at=resource.updated_at,
    )
    if changed == "hash":
        stale.hash = "previous-hash"
    elif changed == "value":
        stale.value = {**desired, "activation_barrier": True}
    elif changed == "full_hash":
        stale.full_hash = "previous-full-hash"
    else:
        stale.tracked_at = resource.updated_at - datetime.timedelta(seconds=1)
    current = ua.TargetResource.restore_from_simple_view(
        **{**stale.dump_to_simple_view(), "agent": str(agent), "node": str(node)}
    )
    resource.target_resource = stale
    persisted = []
    session = mock.Mock()
    engine = mock.Mock()
    engine._get_session_from_storage.return_value = session if outer_session else None
    engine.get_session.return_value = session
    engine.session_manager = MethodType(engines.AbstractEngine.session_manager, engine)
    with (
        mock.patch.object(em.Resource, "render_target_state", return_value=desired),
        mock.patch.object(em.Resource, "get_provider_element", return_value=element),
        mock.patch.object(em.Resource, "_find_actual_resource", return_value=None),
        mock.patch.object(
            em.Resource, "calculate_full_hash", return_value="current-full-hash"
        ),
        mock.patch.object(em.Resource, "update"),
        mock.patch(
            "restalchemy.storage.sql.orm.ObjectCollection.get_one", return_value=current
        ) as read,
        mock.patch.object(
            ua.TargetResource,
            "update",
            autospec=True,
            side_effect=lambda target, **kw: persisted.append(
                target.dump_to_simple_view()
            ),
        ),
        mock.patch(
            "exordos_core.elements.dm.models.engines.engine_factory.get_engine",
            return_value=engine,
        ),
    ):
        resource.actualize()
    assert len(persisted) == 1
    assert persisted[0]["agent"] == str(agent)
    assert persisted[0]["node"] == str(node)
    assert persisted[0]["value"] == desired
    assert persisted[0]["hash"] == utils.calculate_hash(desired)
    assert persisted[0]["full_hash"] == "current-full-hash"
    assert read.call_args.kwargs["locked"] is True
    assert read.call_args.kwargs["session"] is session
    if outer_session:
        engine.get_session.assert_not_called()
        session.commit.assert_not_called()
        session.close.assert_not_called()
    else:
        engine.get_session.assert_called_once()
        session.commit.assert_called_once()
        session.close.assert_called_once()
