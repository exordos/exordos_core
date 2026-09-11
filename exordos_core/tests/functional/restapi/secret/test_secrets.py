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

import typing as tp

from bazooka import exceptions as bazooka_exc
from gcl_iam.tests.functional import clients as iam_clients
import pytest

from exordos_core.secret.dm import models as secret_models


class TestSecretsUserApi:
    # Utils

    @staticmethod
    def _secret_cmp_shallow(
        secret_foo: tp.Dict[str, tp.Any],
        secret_bar: tp.Dict[str, tp.Any],
    ):
        return all(
            (secret_foo[key] == secret_bar[key])
            for key in (
                "uuid",
                "name",
                "constructor",
            )
        )

    # Tests

    def test_secrets_list(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["secret/secrets"])

        response = client.get(url)

        assert response.status_code == 200
        assert response.json() == []

    def test_secrets_add(
        self,
        secret_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        secret = secret_factory()
        url = client.build_collection_uri(["secret/secrets"])
        response = client.post(url, json=secret)
        output = response.json()

        assert response.status_code == 201
        assert self._secret_cmp_shallow(secret, output)
        assert output["status"] == "NEW"
        client.delete(client.build_resource_uri(["secret/secrets", output["uuid"]]))

    def test_secrets_add_without_value(
        self,
        secret_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        secret = secret_factory(value=None)
        url = client.build_collection_uri(["secret/secrets"])
        response = client.post(url, json=secret)
        output = response.json()

        assert response.status_code == 201

        # Neither a value nor a default: there is nothing to deliver.
        stored = secret_models.Secret.objects.get_one(filters={"uuid": output["uuid"]})
        assert stored.value is None
        assert stored.default_value is None
        assert stored.effective_value is None
        # cleanup
        client.delete(client.build_resource_uri(["secret/secrets", output["uuid"]]))

    def test_secrets_default_value_backs_the_value(
        self,
        secret_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        secret = secret_factory(value=None, default_value="default-token")
        url = client.build_collection_uri(["secret/secrets"])
        response = client.post(url, json=secret)
        output = response.json()
        assert response.status_code == 201

        resource_url = client.build_resource_uri(["secret/secrets", output["uuid"]])

        # The default stands in for the value until one is set.
        stored = secret_models.Secret.objects.get_one(filters={"uuid": output["uuid"]})
        assert stored.value is None
        assert stored.effective_value == "default-token"

        # An explicit value wins over the default.
        response = client.put(resource_url, json={"value": "real-token"})
        assert response.status_code == 200
        stored = secret_models.Secret.objects.get_one(filters={"uuid": output["uuid"]})
        assert stored.effective_value == "real-token"

        # Clearing the value falls back to the default again.
        response = client.put(resource_url, json={"value": None})
        assert response.status_code == 200
        stored = secret_models.Secret.objects.get_one(filters={"uuid": output["uuid"]})
        assert stored.value is None
        assert stored.effective_value == "default-token"
        # cleanup
        client.delete(resource_url)

    def test_secrets_default_value_is_not_readable(
        self,
        secret_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        secret = secret_factory(value=None, default_value="default-token")
        url = client.build_collection_uri(["secret/secrets"])
        response = client.post(url, json=secret)
        output = response.json()
        assert response.status_code == 201

        url = client.build_resource_uri(["secret/secrets", output["uuid"]])
        response = client.get(url)
        assert response.status_code == 200
        assert "default_value" not in response.json()
        # cleanup
        client.delete(url)

    def test_secrets_value_is_not_readable(
        self,
        secret_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        secret = secret_factory(value="super-secret-value")
        url = client.build_collection_uri(["secret/secrets"])
        response = client.post(url, json=secret)
        output = response.json()
        assert response.status_code == 201

        # The value is not part of any read response.
        url = client.build_resource_uri(["secret/secrets", output["uuid"]])
        response = client.get(url)
        assert response.status_code == 200
        assert "value" not in response.json()

        response = client.get(client.build_collection_uri(["secret/secrets"]))
        assert response.status_code == 200
        assert all("value" not in item for item in response.json())

        # It is stored though, so the data plane can pick it up.
        stored = secret_models.Secret.objects.get_one(filters={"uuid": output["uuid"]})
        assert stored.value == "super-secret-value"
        # cleanup
        client.delete(url)

    def test_secrets_update_value(
        self,
        secret_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        secret = secret_factory(value="old-value")
        url = client.build_collection_uri(["secret/secrets"])
        response = client.post(url, json=secret)
        output = response.json()
        assert response.status_code == 201

        url = client.build_resource_uri(["secret/secrets", output["uuid"]])
        response = client.put(url, json={"value": "new-value"})

        assert response.status_code == 200
        # The write is echoed back to the writer that supplied it, and
        # that is the only response the value ever appears in.
        assert response.json()["value"] == "new-value"

        stored = secret_models.Secret.objects.get_one(filters={"uuid": output["uuid"]})
        assert stored.value == "new-value"
        # cleanup
        client.delete(url)

    def test_secrets_update_status_new(
        self,
        secret_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        secret = secret_factory()
        url = client.build_collection_uri(["secret/secrets"])
        response = client.post(url, json=secret)
        output = response.json()
        assert response.status_code == 201

        # Manually change status
        secret_obj = secret_models.Secret.objects.get_one(
            filters={"uuid": output["uuid"]}
        )
        secret_obj.status = "IN_PROGRESS"
        secret_obj.update()

        url = client.build_resource_uri(["secret/secrets", output["uuid"]])
        response = client.get(url)
        assert response.status_code == 200
        assert response.json()["status"] == "IN_PROGRESS"

        response = client.put(url, json={"name": "foo-secret"})
        output = response.json()

        assert response.status_code == 200
        assert output["name"] == "foo-secret"
        assert output["status"] == "NEW"
        # cleanup
        client.delete(url)

    def test_secrets_unable_update_status(
        self,
        secret_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        secret = secret_factory()
        url = client.build_collection_uri(["secret/secrets"])
        response = client.post(url, json=secret)
        output = response.json()
        assert response.status_code == 201

        url = client.build_resource_uri(["secret/secrets", output["uuid"]])
        with pytest.raises(bazooka_exc.ForbiddenError):
            client.put(url, json={"status": "ACTIVE"})
        # cleanup
        client.delete(url)

    def test_secrets_delete(
        self,
        secret_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        secret = secret_factory()
        url = client.build_collection_uri(["secret/secrets"])
        response = client.post(url, json=secret)
        output = response.json()
        assert response.status_code == 201

        url = client.build_resource_uri(["secret/secrets", output["uuid"]])
        response = client.delete(url)
        assert response.status_code == 204

        with pytest.raises(bazooka_exc.NotFoundError):
            client.get(url)
