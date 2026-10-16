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

from contextlib import contextmanager
from unittest import mock
import uuid

from exordos_core.cmd import bootstrap


def test_publication_waits_for_asynchronous_manifest_install():
    spec = {"stand": {"bootstraps": [{"uuid": str(uuid.uuid4())}]}}
    events = []
    resource = mock.Mock()
    visible = False

    def install(name, directory):
        events.append("install:" + name)

    def tick(_):
        nonlocal visible
        visible = True
        events.append("manifest committed")

    @contextmanager
    def transaction():
        assert visible
        events.append("begin publication")
        yield
        events.append("commit publication")

    with (
        mock.patch.object(
            bootstrap.ua_models.NodeEncryptionKey, "get_or_create"
        ) as key,
        mock.patch.object(
            bootstrap, "_install_element_from_bootstrap_repo", side_effect=install
        ),
        mock.patch.object(bootstrap.em_models.Resource, "objects") as objects,
        mock.patch.object(
            bootstrap.em_models.element_engine, "load_from_database"
        ) as load,
        mock.patch.object(bootstrap.time, "sleep", side_effect=tick),
        mock.patch.object(bootstrap.contexts, "Context") as context,
        mock.patch.object(bootstrap.bootstrap_defaults, "add_core_set") as publish,
    ):
        objects.get_one_or_none.side_effect = lambda **_: resource if visible else None
        context.return_value.session_manager.side_effect = transaction
        publish.side_effect = lambda _: events.append("publish")
        load.side_effect = lambda: events.append("load manifest engine")
        bootstrap._publish_core_set(spec, "/manifests")
    key.assert_called_once_with(uuid.UUID(spec["stand"]["bootstraps"][0]["uuid"]))
    assert events == [
        "install:core",
        "install:ecosystem_realm",
        "manifest committed",
        "load manifest engine",
        "begin publication",
        "publish",
        "commit publication",
    ]
