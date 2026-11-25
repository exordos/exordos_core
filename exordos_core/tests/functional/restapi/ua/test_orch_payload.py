#    Copyright 2026 Genesis Corporation.
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#        https://www.apache.org/licenses/LICENSE-2.0

import base64
from contextlib import contextmanager
import threading
import uuid
from wsgiref.simple_server import make_server

from bazooka import exceptions
from gcl_sdk.agents.universal.clients.http.orch import OrchAPI
from gcl_sdk.agents.universal.clients.http.status import StatusAPI
from gcl_sdk.agents.universal.dm import models as ua
from gcl_sdk.clients.http.base import Encryptor
import pytest
from restalchemy.api import resources

from exordos_core.orch_api.api import app
from exordos_core.status_api.api import app as status_app
from exordos_core.tests.functional import utils


@contextmanager
def serve(builder):
    original_routes = dict(resources.ResourceMap.resource_map)
    try:
        with make_server("127.0.0.1", 0, utils.build_app(builder)) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                yield f"http://127.0.0.1:{server.server_port}"
            finally:
                server.shutdown()
                thread.join()
    finally:
        resources.ResourceMap.set_resource_map(original_routes)


def test_payload_access_is_bound_to_node(user_api):
    def provision():
        node = uuid.uuid4()
        key = ua.NodeEncryptionKey.get_or_create(node)
        agent = ua.UniversalAgent(
            uuid=uuid.uuid4(),
            node=node,
            name="fixture",
            capabilities={"capabilities": ["render"]},
            facts={"facts": []},
        )
        agent.insert()
        resource_uuid = uuid.uuid4()
        ua.TargetResource(
            uuid=resource_uuid,
            kind="render",
            res_uuid=ua.TargetResource.gen_res_uuid(resource_uuid, "render"),
            agent=agent.uuid,
            value={"uuid": str(resource_uuid), "content": "test configuration"},
        ).insert()
        return agent, key, resource_uuid

    own_agent, key, resource_uuid = provision()
    other_agent, _, _ = provision()
    encryptor = Encryptor(base64.b64decode(key.private_key), own_agent.node)
    with serve(app.build_wsgi_application) as endpoint:
        client = OrchAPI(endpoint, encryptor=encryptor).agents
        payload = client.get_payload(own_agent.uuid, ua.Payload.empty())
        payload_resources = payload.capabilities["render"]["resources"]
        assert [r["uuid"] for r in payload_resources] == [str(resource_uuid)]
        with pytest.raises(exceptions.ForbiddenError):
            client.get_payload(other_agent.uuid, ua.Payload.empty())


def test_agent_registration_is_bound_to_node(user_api):
    owner = uuid.uuid4()
    key = ua.NodeEncryptionKey.get_or_create(owner)
    encryptor = Encryptor(base64.b64decode(key.private_key), owner)
    with serve(status_app.build_wsgi_application) as endpoint:
        client = StatusAPI(endpoint, encryptor=encryptor).agents
        foreign = ua.UniversalAgent(
            uuid=uuid.uuid4(), node=uuid.uuid4(), name="foreign"
        )
        foreign.insert()
        own = ua.UniversalAgent(uuid=uuid.uuid4(), node=owner, name="own")
        with pytest.raises(exceptions.ForbiddenError):
            client.create(
                ua.UniversalAgent(
                    uuid=uuid.uuid4(), node=foreign.node, name="foreign claim"
                )
            )
        assert client.create(own).node == owner
        assert client.update(own.uuid, node=owner, name="updated").name == "updated"
        with pytest.raises(exceptions.ForbiddenError):
            client.update(own.uuid, node=foreign.node)
        with pytest.raises(exceptions.ForbiddenError):
            client.update(foreign.uuid, node=owner)
