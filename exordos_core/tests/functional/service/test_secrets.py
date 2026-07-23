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

from gcl_iam.tests.functional import clients as iam_clients
from gcl_sdk.agents.universal.dm import models as ua_models

from exordos_core.secret import service
from exordos_core.secret.dm import models
from exordos_core.tests.functional import stubs


class TestSecretsServiceBuilder:
    def setup_method(self) -> None:
        # Run service
        self._service = service.SecretServiceBuilder()

    def teardown_method(self) -> None:
        pass

    def test_new_ssh_key(
        self,
        default_node: tp.Dict[str, tp.Any],
        ssh_key_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        agent = ua_models.UniversalAgent(
            uuid=sys_uuid.UUID(default_node["uuid"]),
            node=sys_uuid.UUID(default_node["uuid"]),
            name="UniversalAgent",
        )
        agent.insert()

        client = user_api_client(auth_user_admin)

        key = ssh_key_factory(
            target_public_key="PUBLIC_KEY",
            target_node=sys_uuid.UUID(default_node["uuid"]),
        )

        url = client.build_collection_uri(["secret/ssh_keys"])
        response = client.post(url, json=key)
        output = response.json()

        assert response.status_code == 201
        assert output["status"] == "NEW"

        self._service._iteration()

        target_resources = stubs.TargetResource.objects.get_all()
        keys = models.SSHKey.objects.get_all()

        assert len(target_resources) == 2
        assert len(keys) == 1
        host_key = [r for r in target_resources if r.kind == "ssh_key_target"][0]
        key = keys[0]

        assert key.status == "IN_PROGRESS"
        assert host_key.status == "IN_PROGRESS"
        assert host_key.value["target_public_key"] == "PUBLIC_KEY"
        assert str(host_key.agent) == default_node["uuid"]

        key_url = client.build_resource_uri(["secret/ssh_keys", output["uuid"]])
        client.delete(key_url)
        agent.delete()

    def test_new_ssh_key_fake_node(
        self,
        default_node: tp.Dict[str, tp.Any],
        ssh_key_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        agent = ua_models.UniversalAgent(
            uuid=sys_uuid.UUID(default_node["uuid"]),
            node=sys_uuid.UUID(default_node["uuid"]),
            name="UniversalAgent",
        )
        agent.insert()

        client = user_api_client(auth_user_admin)

        key = ssh_key_factory(
            target_public_key="PUBLIC_KEY", target_node=sys_uuid.uuid4()
        )

        url = client.build_collection_uri(["secret/ssh_keys"])
        response = client.post(url, json=key)
        output = response.json()

        assert response.status_code == 201
        assert output["status"] == "NEW"

        self._service._iteration()

        target_resources = stubs.TargetResource.objects.get_all()
        keys = models.SSHKey.objects.get_all()

        assert len(target_resources) == 0
        assert len(keys) == 0

        agent.delete()

    def test_in_progress_ssh_keys(
        self,
        default_node: tp.Dict[str, tp.Any],
        ssh_key_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        agent = ua_models.UniversalAgent(
            uuid=sys_uuid.UUID(default_node["uuid"]),
            node=sys_uuid.UUID(default_node["uuid"]),
            name="UniversalAgent",
        )
        agent.insert()

        client = user_api_client(auth_user_admin)

        key = ssh_key_factory(
            target_public_key="PUBLIC_KEY",
            target_node=sys_uuid.UUID(default_node["uuid"]),
        )

        url = client.build_collection_uri(["secret/ssh_keys"])
        response = client.post(url, json=key)
        output = response.json()

        assert response.status_code == 201
        assert output["status"] == "NEW"

        self._service._iteration()

        key = models.SSHKey.objects.get_one()
        assert key.status == "IN_PROGRESS"

        target_resources = stubs.TargetResource.objects.get_all()
        host_key = [r for r in target_resources if r.kind == "ssh_key_target"][0]
        view = host_key.dump_to_simple_view()
        view.pop("master", None)
        view.pop("master_hash", None)
        view.pop("master_full_hash", None)
        view.pop("agent", None)
        view.pop("tracked_at", None)
        view["status"] = "ACTIVE"
        view["full_hash"] = "1111"
        host_actual_resource = ua_models.Resource.restore_from_simple_view(**view)
        host_actual_resource.insert()

        self._service._iteration()

        config = models.SSHKey.objects.get_one()
        assert config.status == "ACTIVE"

        key_url = client.build_resource_uri(["secret/ssh_keys", output["uuid"]])
        client.delete(key_url)
        agent.delete()
        host_actual_resource.delete()

    def test_update_ssh_keys(
        self,
        default_node: tp.Dict[str, tp.Any],
        ssh_key_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        agent = ua_models.UniversalAgent(
            uuid=sys_uuid.UUID(default_node["uuid"]),
            node=sys_uuid.UUID(default_node["uuid"]),
            name="UniversalAgent",
        )
        agent.insert()

        client = user_api_client(auth_user_admin)

        key = ssh_key_factory(
            target_public_key="PUBLIC_KEY",
            target_node=sys_uuid.UUID(default_node["uuid"]),
        )

        url = client.build_collection_uri(["secret/ssh_keys"])
        response = client.post(url, json=key)
        output = response.json()

        assert response.status_code == 201
        assert output["status"] == "NEW"
        key_uuid = output["uuid"]

        self._service._iteration()

        key = models.SSHKey.objects.get_one()
        assert key.status == "IN_PROGRESS"

        target_resources = stubs.TargetResource.objects.get_all()
        host_key = [r for r in target_resources if r.kind == "ssh_key_target"][0]
        view = host_key.dump_to_simple_view()
        view.pop("master", None)
        view.pop("master_hash", None)
        view.pop("master_full_hash", None)
        view.pop("agent", None)
        view.pop("tracked_at", None)
        view["status"] = "ACTIVE"
        view["full_hash"] = "1111"
        host_actual_resource = ua_models.Resource.restore_from_simple_view(**view)
        host_actual_resource.insert()

        self._service._iteration()

        key = models.SSHKey.objects.get_one()
        assert key.status == "ACTIVE"

        update = {"user": "debian"}
        url = client.build_resource_uri(["secret/ssh_keys", str(key.uuid)])
        response = client.put(url, json=update)
        assert response.status_code == 200

        output = response.json()
        assert output["user"] == "debian"

        key = models.SSHKey.objects.get_one()
        assert key.status == "NEW"

        self._service._iteration()

        key = models.SSHKey.objects.get_one()
        assert key.status == "IN_PROGRESS"

        key_url = client.build_resource_uri(["secret/ssh_keys", key_uuid])
        client.delete(key_url)
        agent.delete()
        host_actual_resource.delete()

    def test_delete_ssh_keys(
        self,
        default_node: tp.Dict[str, tp.Any],
        ssh_key_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        agent = ua_models.UniversalAgent(
            uuid=sys_uuid.UUID(default_node["uuid"]),
            node=sys_uuid.UUID(default_node["uuid"]),
            name="UniversalAgent",
        )
        agent.insert()

        client = user_api_client(auth_user_admin)

        key = ssh_key_factory(
            target_public_key="PUBLIC_KEY",
            target_node=sys_uuid.UUID(default_node["uuid"]),
        )

        url = client.build_collection_uri(["secret/ssh_keys"])
        response = client.post(url, json=key)
        output = response.json()

        assert response.status_code == 201
        assert output["status"] == "NEW"

        self._service._iteration()

        key = models.SSHKey.objects.get_one()
        assert key.status == "IN_PROGRESS"

        key.delete()

        self._service._iteration()

        target_resources = stubs.TargetResource.objects.get_all()
        assert len(target_resources) == 0

        agent.delete()
