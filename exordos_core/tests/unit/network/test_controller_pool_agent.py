# Copyright 2026 Genesis Corporation.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0

from types import SimpleNamespace
from unittest import mock
import uuid

from gcl_sdk.agents.universal.dm import models
from gcl_sdk.agents.universal.services.agent import UniversalAgentService
import pytest

from exordos_core.gservice import service as gservice


@pytest.fixture
def controller(monkeypatch):
    host = uuid.uuid4()
    driver = mock.Mock()
    driver.get_capabilities.return_value = ["pool"]
    monkeypatch.setattr(gservice.ua_utils, "system_uuid", lambda: host)
    monkeypatch.setattr(gservice.ua_utils, "node_uuid", lambda: host)
    monkeypatch.setattr(gservice, "ControllerPoolAgentDriver", lambda **kw: driver)
    monkeypatch.setattr(
        gservice.ua_core_drivers, "DatabaseCapabilityDriver", mock.Mock()
    )
    monkeypatch.setattr(
        gservice,
        "CONF",
        {
            gservice.event_c.DOMAIN: SimpleNamespace(enabled=False),
            gservice.metrics_opts.DOMAIN: SimpleNamespace(enabled=False),
        },
    )
    service = next(
        s
        for s in gservice.GeneralService()._services
        if isinstance(s, UniversalAgentService) and driver in s._caps_drivers
    )
    return host, service


def test_colocated_pool_agents_preserve_each_others_observations(
    controller, monkeypatch
):
    host, cloud = controller
    agents, resources = {}, {}
    orch = mock.Mock()
    orch.agents_create.side_effect = lambda agent, **kw: agents.setdefault(
        agent.uuid, agent
    )
    orch.resources_create.side_effect = lambda r: resources.setdefault(
        (r.kind, r.uuid), r
    )
    orch.resources_delete.side_effect = lambda r: resources.pop((r.kind, r.uuid))

    def get_all(collection, filters):
        rows = (
            [] if collection.model_cls is models.TargetResource else resources.values()
        )
        return [
            row
            for row in rows
            if all(
                str(getattr(row, key)) == str(condition.value)
                if not isinstance(condition.value, (list, tuple, set))
                else getattr(row, key) in condition.value
                for key, condition in filters.items()
            )
        ]

    monkeypatch.setattr(models.Resource._ObjectCollection, "get_all", get_all)
    cloud._orch_client = orch
    native = UniversalAgentService(
        agent_uuid=host,
        system_uuid=host,
        orch_client=orch,
        caps_drivers=cloud._caps_drivers,
        facts_drivers=[],
        payload_path=None,
    )
    pairs = []
    for service in (cloud, native):
        service._register_agent()
        resource = models.Resource.from_value(
            {"uuid": str(uuid.uuid4()), "status": "ACTIVE"},
            "pool",
            target_fields=frozenset({"uuid"}),
        ).dump_to_simple_view()
        service._actualize_resource_facts([resource], [])
        pairs.append((service, resource))

    for _ in range(3):
        for service, resource in reversed(pairs):
            payload = agents[service._agent_uuid].get_payload()
            service._actualize_facts(
                {"pool": {"resources": [resource]}}, payload.facts, {"pool"}
            )
            assert len(resources) == 2
    assert agents[cloud._agent_uuid].node != agents[native._agent_uuid].node


def test_controller_registration_does_not_require_a_compute_node(controller):
    _, cloud = controller
    cloud._orch_client = mock.Mock()
    cloud._register_agent()
    assert (
        cloud._orch_client.agents_create.call_args.kwargs["check_node_exists"] is False
    )
