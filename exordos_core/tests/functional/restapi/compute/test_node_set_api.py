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

from exordos_core.compute import constants as nc


class TestNodeSetUserApi:
    # Utils

    @staticmethod
    def _node_cmp_shallow(
        node_foo: tp.Dict[str, tp.Any],
        node_bar: tp.Dict[str, tp.Any],
    ):
        return (
            all(
                (node_foo[key] == node_bar[key])
                for key in (
                    "uuid",
                    "name",
                    "cores",
                    "ram",
                    "status",
                    "node_type",
                )
            )
            and node_foo["disk_spec"] == node_bar["disk_spec"]
        )

    @staticmethod
    def _node_set_cmp_shallow(
        node_set_foo: tp.Dict[str, tp.Any],
        node_set_bar: tp.Dict[str, tp.Any],
    ):
        return (
            all(
                (node_set_foo[key] == node_set_bar[key])
                for key in (
                    "uuid",
                    "name",
                    "cores",
                    "ram",
                    "node_type",
                    "replicas",
                )
            )
            and node_set_foo["disk_spec"] == node_set_bar["disk_spec"]
        )

    # Node Sets

    def test_node_sets_list(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["compute", "sets"])

        response = client.get(url)

        assert response.status_code == 200
        assert len(response.json()) == 0

    def test_node_sets_add(
        self,
        node_set_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        node_set = node_set_factory()
        node_set.pop("nodes", None)
        node_set.pop("status", None)

        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["compute", "sets"])

        response = client.post(url, json=node_set)
        output = response.json()

        assert response.status_code == 201
        assert self._node_set_cmp_shallow(node_set, output)
        assert output["status"] == nc.NodeStatus.NEW.value

        client.delete(client.build_resource_uri(["compute", "sets", node_set["uuid"]]))

    def test_node_sets_add_several(
        self,
        node_set_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        node_sets = []
        for i in range(3):
            node_set = node_set_factory(name=f"node_set{i}")
            node_set.pop("nodes", None)
            node_set.pop("status", None)
            node_sets.append(node_set)

        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["compute", "sets"])

        for node_set in node_sets:
            response = client.post(url, json=node_set)
            output = response.json()
            assert response.status_code == 201
            assert self._node_set_cmp_shallow(node_set, output)

        response = client.get(url)
        output = response.json()
        assert len(output) == len(node_sets)
        for node_set in node_sets:
            assert any(self._node_set_cmp_shallow(node_set, item) for item in output)

        for ns in node_sets:
            client.delete(client.build_resource_uri(["compute", "sets", ns["uuid"]]))

    def test_node_sets_update(
        self,
        node_set_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        node_set = node_set_factory()
        node_set.pop("nodes", None)
        node_set.pop("status", None)

        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["compute", "sets"])

        response = client.post(url, json=node_set)
        output = response.json()
        assert response.status_code == 201

        update = {"cores": 2, "ram": 2048}
        url = client.build_resource_uri(["compute", "sets", node_set["uuid"]])
        response = client.put(url, json=update)
        output = response.json()

        assert response.status_code == 200
        assert output["cores"] == 2
        assert output["ram"] == 2048

        client.delete(client.build_resource_uri(["compute", "sets", node_set["uuid"]]))

    def test_node_sets_delete(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        node_set_factory: tp.Callable,
    ):
        node_set = node_set_factory()
        node_set.pop("nodes", None)
        node_set.pop("status", None)

        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["compute", "sets"])
        response = client.post(url, json=node_set)
        assert response.status_code == 201

        url = client.build_resource_uri(["compute", "sets", node_set["uuid"]])
        response = client.delete(url)

        assert response.status_code == 204

        url = client.build_resource_uri(["compute", "sets", node_set["uuid"]])
        with pytest.raises(bazooka_exc.NotFoundError):
            response = client.get(url)

    def test_newcomer_no_access(
        self,
        node_set_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        auth_test1_user: iam_clients.GenesisCoreAuth,
    ):
        admin_client = user_api_client(auth_user_admin)

        node_set = node_set_factory()
        node_set.pop("nodes", None)
        node_set.pop("status", None)

        set_uuid = node_set["uuid"]
        url = admin_client.build_collection_uri(["compute", "sets"])
        response = admin_client.post(url, json=node_set)

        assert response.status_code == 201

        client = user_api_client(auth_test1_user)

        node_set = node_set_factory()
        node_set.pop("nodes", None)
        node_set.pop("status", None)
        url = client.build_collection_uri(["compute", "sets"])

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.get(url)

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.post(url, json=node_set)

        url = client.build_resource_uri(["compute", "sets", set_uuid])
        with pytest.raises(bazooka_exc.ForbiddenError):
            client.delete(url)

        url = admin_client.build_collection_uri(["compute", "sets"])
        response = admin_client.get(url)

        assert response.status_code == 200
        assert len(response.json()) == 1

        admin_client.delete(
            admin_client.build_resource_uri(["compute", "sets", set_uuid])
        )

    def test_owner_has_access(
        self,
        node_set_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        auth_test1_p1_user: iam_clients.GenesisCoreAuth,
    ):
        admin_client = user_api_client(auth_user_admin)
        admin_client.bind_role_to_user(
            # Owner role
            "726f6c65-0000-0000-0000-000000000002",
            auth_test1_p1_user.uuid,
            auth_test1_p1_user.project_id,
        )

        client = user_api_client(auth_test1_p1_user)
        node_set = node_set_factory(
            project_id=sys_uuid.UUID(auth_test1_p1_user.project_id)
        )
        node_set.pop("nodes", None)
        node_set.pop("status", None)

        set_uuid = node_set["uuid"]
        url = client.build_collection_uri(["compute", "sets"])
        response = admin_client.post(url, json=node_set)

        assert response.status_code == 201

        url = client.build_collection_uri(["compute", "sets"])
        response = client.get(url)
        assert response.status_code == 200

        output = response.json()
        assert len(output) == 1
        assert self._node_set_cmp_shallow(node_set, output[0])

        url = client.build_resource_uri(["compute", "sets", set_uuid])
        response = client.delete(url)
        assert response.status_code == 204
