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

from exordos_core.secret.dm import models as secret_models


class TestSSHKeysUserApi:
    # Utils

    @staticmethod
    def _secret_cmp_shallow(
        cfg_foo: tp.Dict[str, tp.Any],
        cfg_bar: tp.Dict[str, tp.Any],
    ):
        return all(
            (cfg_foo[key] == cfg_bar[key])
            for key in (
                "uuid",
                "name",
                "description",
                "constructor",
                "target",
                "user",
                "authorized_keys",
                "target_public_key",
            )
        )

    # Tests

    def test_ssh_keys_list(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["secret/ssh_keys"])

        response = client.get(url)

        assert response.status_code == 200
        assert len(response.json()) == 0

    def test_ssh_keys_add(
        self,
        default_node: tp.Dict[str, tp.Any],
        ssh_key_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        key = ssh_key_factory(
            target_public_key="PUBLIC_KEY",
            target_node=sys_uuid.UUID(default_node["uuid"]),
        )
        url = client.build_collection_uri(["secret/ssh_keys"])
        response = client.post(url, json=key)
        output = response.json()

        assert response.status_code == 201
        assert self._secret_cmp_shallow(key, output)
        client.delete(client.build_resource_uri(["secret/ssh_keys", output["uuid"]]))

    def test_ssh_keys_add_several(
        self,
        default_node: tp.Dict[str, tp.Any],
        ssh_key_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        uuids = []
        url = client.build_collection_uri(["secret/ssh_keys"])
        for i in range(3):
            key = ssh_key_factory(
                target_public_key="PUBLIC_KEY_" + str(i),
                target_node=sys_uuid.UUID(default_node["uuid"]),
            )
            response = client.post(url, json=key)
            output = response.json()
            assert response.status_code == 201
            assert self._secret_cmp_shallow(key, output)
            uuids.append(output["uuid"])

        for uuid in uuids:
            response = client.get(client.build_resource_uri(["secret/ssh_keys", uuid]))
            assert response.status_code == 200
            client.delete(client.build_resource_uri(["secret/ssh_keys", uuid]))

    def test_ssh_keys_add_same(
        self,
        default_node: tp.Dict[str, tp.Any],
        ssh_key_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        key = ssh_key_factory(
            target_public_key="PUBLIC_KEY",
            target_node=sys_uuid.UUID(default_node["uuid"]),
        )
        url = client.build_collection_uri(["secret/ssh_keys"])
        response = client.post(url, json=key)
        output = response.json()

        assert response.status_code == 201
        assert self._secret_cmp_shallow(key, output)

        key = ssh_key_factory(
            target_public_key="PUBLIC_KEY",
            target_node=sys_uuid.UUID(default_node["uuid"]),
        )

        with pytest.raises(bazooka_exc.ConflictError):
            client.post(url, json=key)

        client.delete(client.build_resource_uri(["secret/ssh_keys", output["uuid"]]))

    def test_ssh_keys_update(
        self,
        default_node: tp.Dict[str, tp.Any],
        ssh_key_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        key = ssh_key_factory(
            target_public_key="PUBLIC_KEY",
            target_node=sys_uuid.UUID(default_node["uuid"]),
        )
        url = client.build_collection_uri(["secret/ssh_keys"])
        response = client.post(url, json=key)
        output = response.json()

        assert response.status_code == 201
        assert self._secret_cmp_shallow(key, output)

        update = {"name": "foo-key"}
        url = client.build_resource_uri(["secret/ssh_keys", output["uuid"]])
        response = client.put(url, json=update)
        output = response.json()

        assert response.status_code == 200
        assert output["name"] == "foo-key"

        response = client.get(url)
        output = response.json()
        assert response.status_code == 200
        assert output["name"] == "foo-key"
        client.delete(url)

    def test_ssh_keys_update_status_new(
        self,
        default_node: tp.Dict[str, tp.Any],
        ssh_key_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        key = ssh_key_factory(
            target_public_key="PUBLIC_KEY",
            target_node=sys_uuid.UUID(default_node["uuid"]),
        )
        url = client.build_collection_uri(["secret/ssh_keys"])
        response = client.post(url, json=key)
        output = response.json()

        assert response.status_code == 201
        assert self._secret_cmp_shallow(key, output)

        # Manually change status
        key_obj = secret_models.SSHKey.objects.get_one(filters={"uuid": output["uuid"]})
        key_obj.status = "IN_PROGRESS"
        key_obj.update()

        url = client.build_resource_uri(["secret/ssh_keys", output["uuid"]])
        response = client.get(url)
        output = response.json()
        assert response.status_code == 200
        assert output["status"] == "IN_PROGRESS"

        update = {"name": "foo-key"}
        url = client.build_resource_uri(["secret/ssh_keys", output["uuid"]])
        response = client.put(url, json=update)
        output = response.json()

        assert response.status_code == 200
        assert output["name"] == "foo-key"
        assert output["status"] == "NEW"
        client.delete(url)

    def test_ssh_keys_delete(
        self,
        default_node: tp.Dict[str, tp.Any],
        ssh_key_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        key = ssh_key_factory(
            target_public_key="PUBLIC_KEY",
            target_node=sys_uuid.UUID(default_node["uuid"]),
        )
        url = client.build_collection_uri(["secret/ssh_keys"])
        response = client.post(url, json=key)
        output = response.json()

        assert response.status_code == 201
        assert self._secret_cmp_shallow(key, output)

        url = client.build_resource_uri(["secret/ssh_keys", output["uuid"]])
        response = client.delete(url)
        assert response.status_code == 204

        with pytest.raises(bazooka_exc.NotFoundError):
            client.get(url)

    def test_ssh_keys_update_unable_update_status(
        self,
        default_node: tp.Dict[str, tp.Any],
        ssh_key_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        key = ssh_key_factory(
            target_public_key="PUBLIC_KEY",
            target_node=sys_uuid.UUID(default_node["uuid"]),
        )
        url = client.build_collection_uri(["secret/ssh_keys"])
        response = client.post(url, json=key)
        output = response.json()

        assert response.status_code == 201
        assert self._secret_cmp_shallow(key, output)

        update = {"status": "ACTIVE"}
        url = client.build_resource_uri(["secret/ssh_keys", output["uuid"]])
        with pytest.raises(bazooka_exc.ForbiddenError):
            client.put(url, json=update)
        client.delete(client.build_resource_uri(["secret/ssh_keys", output["uuid"]]))
