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

from bazooka import exceptions as bazooka_exc
from gcl_iam.tests.functional import clients as iam_clients
import pytest

from exordos_core.compute import constants as nc


class TestVolumeUserApi:
    @staticmethod
    def _volume_cmp_shallow(
        volume_foo: tp.Dict[str, tp.Any],
        volume_bar: tp.Dict[str, tp.Any],
    ) -> bool:
        return all(
            (volume_foo[key] == volume_bar[key])
            for key in (
                "uuid",
                "name",
                "size",
                "boot",
                "device_type",
            )
        )

    def test_volumes_list(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["compute", "volumes"])

        response = client.get(url)

        assert response.status_code == 200
        assert len(response.json()) == 0

    def test_volumes_add(
        self,
        volume_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        volume = volume_factory()

        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["compute", "volumes"])
        response = client.post(url, json=volume)
        output = response.json()

        assert response.status_code == 201
        assert self._volume_cmp_shallow(volume, output)
        assert output["status"] == nc.VolumeStatus.NEW.value

    def test_volumes_add_several(
        self,
        volume_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        volumes = []
        for i in range(3):
            volume = volume_factory(
                name=f"volume_{i}",
                size=10 * (i + 1),
            )
            volumes.append(volume)

        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["compute", "volumes"])

        for volume in volumes:
            response = client.post(url, json=volume)
            output = response.json()
            assert response.status_code == 201
            assert self._volume_cmp_shallow(volume, output)

        response = client.get(url)
        output = response.json()
        assert len(output) == len(volumes)
        for volume in volumes:
            assert any(self._volume_cmp_shallow(volume, item) for item in output)

    def test_volumes_update(
        self,
        volume_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        volume = volume_factory()

        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["compute", "volumes"])

        response = client.post(url, json=volume)
        assert response.status_code == 201

        update = {"size": 20, "name": "updated-volume"}
        url = client.build_resource_uri(["compute", "volumes", volume["uuid"]])
        response = client.put(url, json=update)
        output = response.json()

        assert response.status_code == 200
        assert output["size"] == 20
        assert output["name"] == "updated-volume"
        assert output["status"] == nc.VolumeStatus.IN_PROGRESS.value

    def test_volumes_delete(
        self,
        volume_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        volume = volume_factory()

        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["compute", "volumes"])
        response = client.post(url, json=volume)
        assert response.status_code == 201

        url = client.build_resource_uri(["compute", "volumes", volume["uuid"]])
        response = client.delete(url)

        assert response.status_code == 204

        url = client.build_resource_uri(["compute", "volumes", volume["uuid"]])
        with pytest.raises(bazooka_exc.NotFoundError):
            client.get(url)

    def test_volume_attach(
        self,
        volume_factory: tp.Callable,
        node_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        volume = volume_factory()
        node = node_factory()

        client = user_api_client(auth_user_admin)

        url = client.build_collection_uri(["compute", "nodes"])
        response = client.post(url, json=node)
        assert response.status_code == 201

        url = client.build_collection_uri(["compute", "volumes"])
        response = client.post(url, json=volume)
        assert response.status_code == 201

        attach_url = client.build_resource_uri(
            ["compute", "volumes", volume["uuid"], "actions", "attach", "invoke"]
        )
        response = client.post(attach_url, json={"node": node["uuid"]})
        assert response.status_code == 200
        url = client.build_resource_uri(["compute", "volumes", volume["uuid"]])
        response = client.get(url)
        output = response.json()

        assert response.status_code == 200
        assert output["node"].endswith(node["uuid"])

        client.delete(client.build_resource_uri(["compute", "volumes", volume["uuid"]]))
        client.delete(client.build_resource_uri(["compute", "nodes", node["uuid"]]))

    def test_volume_attach_already_attached(
        self,
        volume_factory: tp.Callable,
        node_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        volume = volume_factory()
        node = node_factory()

        client = user_api_client(auth_user_admin)

        url = client.build_collection_uri(["compute", "nodes"])
        response = client.post(url, json=node)
        assert response.status_code == 201

        url = client.build_collection_uri(["compute", "volumes"])
        response = client.post(url, json=volume)
        assert response.status_code == 201

        attach_url = client.build_resource_uri(
            ["compute", "volumes", volume["uuid"], "actions", "attach", "invoke"]
        )
        response = client.post(attach_url, json={"node": node["uuid"]})
        assert response.status_code == 200

        with pytest.raises(bazooka_exc.BadRequestError) as exc_info:
            client.post(attach_url, json={"node": node["uuid"]})

        assert "already attached" in str(exc_info.value.cause.response.text).lower()

        client.delete(client.build_resource_uri(["compute", "volumes", volume["uuid"]]))
        client.delete(client.build_resource_uri(["compute", "nodes", node["uuid"]]))

    def test_volume_detach(
        self,
        volume_factory: tp.Callable,
        node_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        volume = volume_factory()
        node = node_factory()

        client = user_api_client(auth_user_admin)

        url = client.build_collection_uri(["compute", "nodes"])
        response = client.post(url, json=node)
        assert response.status_code == 201

        url = client.build_collection_uri(["compute", "volumes"])
        response = client.post(url, json=volume)
        assert response.status_code == 201

        attach_url = client.build_resource_uri(
            ["compute", "volumes", volume["uuid"], "actions", "attach", "invoke"]
        )
        response = client.post(attach_url, json={"node": node["uuid"]})
        assert response.status_code == 200

        url = client.build_resource_uri(["compute", "volumes", volume["uuid"]])
        response = client.get(url)
        output = response.json()

        assert response.status_code == 200
        assert output["node"].endswith(node["uuid"])

        detach_url = client.build_resource_uri(
            ["compute", "volumes", volume["uuid"], "actions", "detach", "invoke"]
        )
        response = client.post(detach_url)
        assert response.status_code == 200

        url = client.build_resource_uri(["compute", "volumes", volume["uuid"]])
        response = client.get(url)
        output = response.json()

        assert response.status_code == 200
        assert "node" not in output

        client.delete(client.build_resource_uri(["compute", "volumes", volume["uuid"]]))
        client.delete(client.build_resource_uri(["compute", "nodes", node["uuid"]]))

    def test_volume_detach_not_attached(
        self,
        volume_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        volume = volume_factory()

        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["compute", "volumes"])
        response = client.post(url, json=volume)
        assert response.status_code == 201

        detach_url = client.build_resource_uri(
            ["compute", "volumes", volume["uuid"], "actions", "detach", "invoke"]
        )

        with pytest.raises(bazooka_exc.BadRequestError) as exc_info:
            client.post(detach_url)

        assert "not attached" in str(exc_info.value.cause.response.text).lower()

        client.delete(client.build_resource_uri(["compute", "volumes", volume["uuid"]]))

    def test_newcomer_no_access(
        self,
        volume_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        auth_test1_user: iam_clients.GenesisCoreAuth,
    ):
        admin_client = user_api_client(auth_user_admin)

        volume = volume_factory()
        volume_uuid = volume["uuid"]
        url = admin_client.build_collection_uri(["compute", "volumes"])
        response = admin_client.post(url, json=volume)

        assert response.status_code == 201

        client = user_api_client(auth_test1_user)

        volume = volume_factory()
        url = client.build_collection_uri(["compute", "volumes"])

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.get(url)

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.post(url, json=volume)

        url = client.build_resource_uri(["compute", "volumes", volume_uuid])
        with pytest.raises(bazooka_exc.ForbiddenError):
            client.delete(url)

        url = admin_client.build_collection_uri(["compute", "volumes"])
        response = admin_client.get(url)

        assert response.status_code == 200
        assert len(response.json()) == 1

        admin_client.delete(
            admin_client.build_resource_uri(["compute", "volumes", volume_uuid])
        )
