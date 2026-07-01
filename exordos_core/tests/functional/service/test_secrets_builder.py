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

from gcl_iam.tests.functional import clients as iam_clients
import pytest

from exordos_core.secret import constants as sc
from exordos_core.tests.functional import stubs


class TestSecretsServiceBuilder:
    def test_no_secrets(
        self,
        agent_service,
        password_builder,
    ):
        password_builder._iteration()
        agent_service._iteration()

    @pytest.mark.parametrize(
        "secret_method",
        [
            sc.SecretMethod.AUTO_HEX,
            sc.SecretMethod.AUTO_URL_SAFE,
            sc.SecretMethod.MANUAL,
        ],
    )
    def test_create_password(
        self,
        default_node: tp.Dict[str, tp.Any],
        password_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        agent_service,
        password_builder,
        universal_scheduler,
        secret_method,
    ):
        client = user_api_client(auth_user_admin)

        url = client.build_collection_uri(["secret/passwords"])

        if secret_method == sc.SecretMethod.MANUAL:
            password = password_factory(
                method=secret_method, value="my-strong-password-value"
            )
        else:
            password = password_factory(method=secret_method)

        response = client.post(url, json=password)
        output = response.json()

        assert response.status_code == 201
        assert output["status"] == "NEW"

        password_builder._iteration()
        universal_scheduler._iteration()
        agent_service._iteration()

        target_resources = stubs.TargetResource.objects.get_all()
        passwords = stubs.Password.objects.get_all()

        assert len(target_resources) == 1
        assert len(passwords) == 1
        password = passwords[0]

        assert password.status == "IN_PROGRESS"

        password_builder._iteration()
        agent_service._iteration()

        target_resources = stubs.TargetResource.objects.get_all()
        passwords = stubs.Password.objects.get_all()

        assert len(target_resources) == 1
        assert len(passwords) == 1
        password = passwords[0]

        assert password.status == "ACTIVE"

        url = client.build_resource_uri(["secret/passwords", str(password.uuid)])
        response = client.delete(url)
        assert response.status_code == 204

    def test_update_password(
        self,
        default_node: tp.Dict[str, tp.Any],
        password_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        agent_service,
        password_builder,
        universal_scheduler,
    ):
        client = user_api_client(auth_user_admin)

        password = password_factory()

        url = client.build_collection_uri(["secret/passwords"])
        client.post(url, json=password)

        password_builder._iteration()
        universal_scheduler._iteration()
        agent_service._iteration()

        password_builder._iteration()
        agent_service._iteration()

        password = stubs.Password.objects.get_one()
        assert password.status == "ACTIVE"
        old_value = password.value
        assert len(old_value) == 32

        default_length = 34
        update = {"default_length": default_length}
        url = client.build_resource_uri(["secret/passwords", str(password.uuid)])
        response = client.put(url, json=update)
        assert response.status_code == 200

        password = stubs.Password.objects.get_one()
        assert password.status == "NEW"

        password_builder._iteration()
        agent_service._iteration()

        password_builder._iteration()
        agent_service._iteration()

        password = stubs.Password.objects.get_one()
        assert password.status == "ACTIVE"

        new_value = password.value
        assert old_value != new_value
        assert len(new_value) == default_length

        url = client.build_resource_uri(["secret/passwords", str(password.uuid)])
        response = client.delete(url)
        assert response.status_code == 204

    def test_delete_password(
        self,
        default_node: tp.Dict[str, tp.Any],
        password_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        agent_service,
        password_builder,
        universal_scheduler,
    ):
        client = user_api_client(auth_user_admin)

        password = password_factory()

        url = client.build_collection_uri(["secret/passwords"])
        client.post(url, json=password)

        password_builder._iteration()
        universal_scheduler._iteration()
        agent_service._iteration()

        password_builder._iteration()
        agent_service._iteration()

        password = stubs.Password.objects.get_one()
        assert password.status == "ACTIVE"

        url = client.build_resource_uri(["secret/passwords", str(password.uuid)])
        response = client.delete(url)
        assert response.status_code == 204

        password_builder._iteration()
        agent_service._iteration()

        target_resources = stubs.TargetResource.objects.get_all()
        assert len(target_resources) == 0

        resources = stubs.Resource.objects.get_all()
        assert len(resources) == 0

        passwords = stubs.Password.objects.get_all()
        assert len(passwords) == 0

        storage_passwords = stubs.StoragePassword.objects.get_all()
        assert len(storage_passwords) == 0
