#    Copyright 2025 Genesis Corporation.
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

import typing as tp
import uuid as sys_uuid

from bazooka import exceptions as bazooka_exc
from gcl_iam.tests.functional import clients as iam_clients
import pytest

from exordos_core.config.dm import models


class TestConfigUserApi:
    # Utils

    @staticmethod
    def _config_cmp_shallow(
        cfg_foo: tp.Dict[str, tp.Any],
        cfg_bar: tp.Dict[str, tp.Any],
    ):
        return all(
            (cfg_foo[key] == cfg_bar[key])
            for key in (
                "uuid",
                "name",
                "path",
                "target",
                "body",
                "on_change",
                "status",
            )
        )

    # Tests

    def test_configs_list(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["config/configs"])

        response = client.get(url)

        assert response.status_code == 200
        assert len(response.json()) == 0

    def test_configs_add(
        self,
        node_factory: tp.Callable,
        config_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        node = node_factory()
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["compute", "nodes"])

        response = client.post(url, json=node)
        assert response.status_code == 201

        config = config_factory(target_node=sys_uuid.UUID(node["uuid"]))
        url = client.build_collection_uri(["config/configs"])
        response = client.post(url, json=config)
        output = response.json()

        assert response.status_code == 201
        assert self._config_cmp_shallow(config, output)

    def test_configs_add_several(
        self,
        node_factory: tp.Callable,
        config_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        node = node_factory()
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["compute", "nodes"])

        response = client.post(url, json=node)
        assert response.status_code == 201

        urls = []
        for i in range(3):
            config = config_factory(
                target_node=sys_uuid.UUID(node["uuid"]),
                path=f"/tmp/{i}.conf",
            )
            url = client.build_collection_uri(["config/configs"])
            response = client.post(url, json=config)
            output = response.json()

            assert response.status_code == 201
            assert self._config_cmp_shallow(config, output)
            urls.append(url)

        for url in urls:
            response = client.get(url)
            assert response.status_code == 200

    def test_configs_add_same_path(
        self,
        node_factory: tp.Callable,
        config_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        node = node_factory()
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["compute", "nodes"])

        response = client.post(url, json=node)
        assert response.status_code == 201

        config1 = config_factory(
            target_node=sys_uuid.UUID(node["uuid"]),
            path="/tmp/config.conf",
        )
        url = client.build_collection_uri(["config/configs"])
        response = client.post(url, json=config1)
        assert response.status_code == 201

        config2 = config_factory(
            target_node=sys_uuid.UUID(node["uuid"]),
            path="/tmp/config.conf",
        )

        with pytest.raises(bazooka_exc.ConflictError):
            client.post(url, json=config2)

    def test_configs_update(
        self,
        node_factory: tp.Callable,
        config_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        node = node_factory()
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["compute", "nodes"])

        response = client.post(url, json=node)
        assert response.status_code == 201

        config = config_factory(target_node=sys_uuid.UUID(node["uuid"]))
        url = client.build_collection_uri(["config/configs"])
        response = client.post(url, json=config)
        output = response.json()

        assert response.status_code == 201
        assert self._config_cmp_shallow(config, output)

        update = {"path": "/tmp/updated.conf"}
        url = client.build_resource_uri(["config/configs", output["uuid"]])

        response = client.put(url, json=update)
        output = response.json()

        assert response.status_code == 200
        assert output["path"] == "/tmp/updated.conf"
        client.delete(client.build_resource_uri(["config/configs", output["uuid"]]))

    def test_configs_update_status_new(
        self,
        node_factory: tp.Callable,
        config_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        node = node_factory()
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["compute", "nodes"])

        response = client.post(url, json=node)
        assert response.status_code == 201

        config = config_factory(target_node=sys_uuid.UUID(node["uuid"]))
        url = client.build_collection_uri(["config/configs"])
        response = client.post(url, json=config)
        output = response.json()

        assert response.status_code == 201

        # Manually change status
        config_obj = models.Config.objects.get_one(filters={"uuid": output["uuid"]})
        config_obj.status = "IN_PROGRESS"
        config_obj.update()

        update = {"path": "/tmp/updated.conf"}
        url = client.build_resource_uri(["config/configs", output["uuid"]])

        response = client.put(url, json=update)
        output = response.json()

        assert response.status_code == 200
        assert output["path"] == "/tmp/updated.conf"
        assert output["status"] == "NEW"

    def test_configs_delete(
        self,
        node_factory: tp.Callable,
        config_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        node = node_factory()
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["compute", "nodes"])

        response = client.post(url, json=node)
        assert response.status_code == 201

        config = config_factory(target_node=sys_uuid.UUID(node["uuid"]))
        url = client.build_collection_uri(["config/configs"])
        response = client.post(url, json=config)
        output = response.json()

        assert response.status_code == 201
        assert self._config_cmp_shallow(config, output)

        url = client.build_resource_uri(["config/configs", output["uuid"]])
        response = client.delete(url)
        assert response.status_code == 204

        with pytest.raises(bazooka_exc.NotFoundError):
            client.get(url)
