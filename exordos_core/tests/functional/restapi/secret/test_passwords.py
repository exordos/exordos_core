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

from exordos_core.secret import constants as sc
from exordos_core.secret.dm import models as secret_models


class TestPasswordsUserApi:
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
                "method",
                "constructor",
            )
        )

    # Tests

    def test_passwords_list(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["secret/passwords"])

        response = client.get(url)

        assert response.status_code == 200
        assert len(response.json()) == 1

    def test_passwords_add(
        self,
        password_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        password = password_factory()
        url = client.build_collection_uri(["secret/passwords"])
        response = client.post(url, json=password)
        output = response.json()

        assert response.status_code == 201
        assert self._secret_cmp_shallow(password, output)
        client.delete(client.build_resource_uri(["secret/passwords", output["uuid"]]))

    def test_passwords_add_several(
        self,
        password_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        uuids = []
        url = client.build_collection_uri(["secret/passwords"])
        for i in range(3):
            password = password_factory()
            response = client.post(url, json=password)
            output = response.json()
            assert response.status_code == 201
            assert self._secret_cmp_shallow(password, output)
            uuids.append(output["uuid"])

        for uuid in uuids:
            response = client.get(client.build_resource_uri(["secret/passwords", uuid]))
            assert response.status_code == 200
            client.delete(client.build_resource_uri(["secret/passwords", uuid]))

    def test_passwords_add_not_default(
        self,
        password_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        password = password_factory(
            method=sc.SecretMethod.AUTO_URL_SAFE,
            constructor=secret_models.PlainSecretConstructor(),
        )
        url = client.build_collection_uri(["secret/passwords"])
        response = client.post(url, json=password)
        output = response.json()

        assert response.status_code == 201
        assert output["method"] == sc.SecretMethod.AUTO_URL_SAFE
        assert output["constructor"]["kind"] == "plain"
        client.delete(client.build_resource_uri(["secret/passwords", output["uuid"]]))

    def test_passwords_update(
        self,
        password_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        password = password_factory()
        url = client.build_collection_uri(["secret/passwords"])
        response = client.post(url, json=password)
        output = response.json()

        assert response.status_code == 201
        assert self._secret_cmp_shallow(password, output)

        update = {"name": "foo-password"}
        url = client.build_resource_uri(["secret/passwords", output["uuid"]])
        response = client.put(url, json=update)
        output = response.json()

        assert response.status_code == 200
        assert output["name"] == "foo-password"

        response = client.get(url)
        output = response.json()
        assert response.status_code == 200
        assert output["name"] == "foo-password"
        # cleanup
        client.delete(url)

    def test_passwords_update_status_new(
        self,
        password_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        password = password_factory()
        url = client.build_collection_uri(["secret/passwords"])
        response = client.post(url, json=password)
        output = response.json()

        assert response.status_code == 201
        assert self._secret_cmp_shallow(password, output)

        # Manually change status
        password_obj = secret_models.Password.objects.get_one(
            filters={"uuid": output["uuid"]}
        )
        password_obj.status = "IN_PROGRESS"
        password_obj.update()

        url = client.build_resource_uri(["secret/passwords", output["uuid"]])
        response = client.get(url)
        output = response.json()
        assert response.status_code == 200
        assert output["status"] == "IN_PROGRESS"

        update = {"name": "foo-password"}
        url = client.build_resource_uri(["secret/passwords", output["uuid"]])
        response = client.put(url, json=update)
        output = response.json()

        assert response.status_code == 200
        assert output["name"] == "foo-password"
        assert output["status"] == "NEW"
        # cleanup
        client.delete(url)

    def test_passwords_delete(
        self,
        password_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        password = password_factory()
        url = client.build_collection_uri(["secret/passwords"])
        response = client.post(url, json=password)
        output = response.json()

        assert response.status_code == 201
        assert self._secret_cmp_shallow(password, output)

        url = client.build_resource_uri(["secret/passwords", output["uuid"]])
        response = client.delete(url)
        assert response.status_code == 204

        with pytest.raises(bazooka_exc.NotFoundError):
            client.get(url)
