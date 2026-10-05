#    Copyright 2026 Genesis Corporation.
#
#    All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
import contextlib
import uuid
from unittest import mock

from exordos_core.config import service


def test_config_delivery_does_not_query_agents():
    builder = service.ConfigServiceBuilder()
    node = mock.Mock(uuid=uuid.uuid4())
    config = mock.Mock()
    config.target.are_owners_alive.return_value = True
    with mock.patch.object(service.ua_models.UniversalAgent, "objects") as agents:
        builder._actualize_new_config(config, [node])
        agents.get_all.assert_not_called()
    config.to_ua_resource.return_value.insert.assert_called_once()
    config.render.return_value.insert.assert_called_once()
    config.save.assert_called_once()


def test_failed_config_rolls_back_before_next_row():
    builder = service.ConfigServiceBuilder()
    nodes = [mock.Mock(uuid=uuid.uuid4())]
    configs = [mock.Mock(), mock.Mock()]
    for config in configs:
        config.target_nodes.return_value = [nodes[0].uuid]
    poisoned = False
    applied = []

    @contextlib.contextmanager
    def savepoint():
        nonlocal poisoned
        try:
            yield
        except Exception:
            poisoned = False
            raise

    def actualize(config, target_nodes):
        nonlocal poisoned
        assert not poisoned, "Previous config left an aborted transaction"
        if config is configs[0]:
            poisoned = True
            raise RuntimeError("Foreign key violation")
        applied.append(config)

    with (
        mock.patch.object(service.node_models.Node, "objects") as objects,
        mock.patch.object(service.sql_utils, "savepoint", savepoint),
        mock.patch.object(builder, "_actualize_new_config", actualize),
    ):
        objects.get_all.return_value = nodes
        builder._actualize_new_configs(configs)
    assert applied == [configs[1]]
    assert not poisoned
