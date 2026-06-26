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

from exordos_core.user_api.iam import constants as iam_c


class TestUaAgentsApi:
    @staticmethod
    def _agent_factory(
        uuid: tp.Optional[sys_uuid.UUID] = None,
        name: tp.Optional[str] = None,
        status: str = "ACTIVE",
        **kwargs,
    ) -> sys_uuid.UUID:
        from gcl_sdk.agents.universal.dm import models as ua_models

        uuid = uuid or sys_uuid.uuid4()
        name = name or f"test_agent_{str(uuid)[:8]}"
        agent = ua_models.UniversalAgent(
            uuid=uuid,
            name=name,
            capabilities={"capabilities": ["test_capability"]},
            facts={"facts": []},
            node=sys_uuid.uuid4(),
            status=status,
            **kwargs,
        )
        agent.insert()
        return uuid

    def test_admin_list_agents(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        agent_uuid = self._agent_factory()
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["ua", "agents"])

        response = client.get(url)
        output = response.json()

        assert response.status_code == 200
        uuids = [item["uuid"] for item in output]
        assert str(agent_uuid) in uuids

    def test_admin_list_agents_empty(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["ua", "agents"])

        response = client.get(url)

        assert response.status_code == 200
        assert len(response.json()) == 0

    def test_admin_get_agent(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        agent_uuid = self._agent_factory()
        client = user_api_client(auth_user_admin)
        url = client.build_resource_uri(["ua", "agents", str(agent_uuid)])

        response = client.get(url)
        output = response.json()

        assert response.status_code == 200
        assert output["uuid"] == str(agent_uuid)

    def test_admin_get_agent_not_found(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        url = client.build_resource_uri(
            ["ua", "agents", "00000000-0000-0000-0000-000000000000"]
        )

        with pytest.raises(bazooka_exc.NotFoundError):
            client.get(url)

    def test_nonadmin_no_access(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_test1_user: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_test1_user)
        url = client.build_collection_uri(["ua", "agents"])

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.get(url)

    def test_unauthorized_no_access(
        self,
        user_api_noauth_client: tp.Callable,
    ):
        client = user_api_noauth_client()
        url = client.build_collection_uri(["ua", "agents"])

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.get(url)

    def test_user_with_permission_can_list(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_test1_user: iam_clients.GenesisCoreAuth,
    ):
        agent_uuid = self._agent_factory()
        client = user_api_client(
            auth_test1_user,
            permissions=[
                iam_c.PERMISSION_WILDCARD_NAME,
            ],
        )
        url = client.build_collection_uri(["ua", "agents"])

        response = client.get(url)
        output = response.json()

        assert response.status_code == 200
        uuids = [item["uuid"] for item in output]
        assert str(agent_uuid) in uuids


class TestUaResourcesApi:
    @staticmethod
    def _resource_factory(
        uuid: tp.Optional[sys_uuid.UUID] = None,
        kind: str = "test_kind",
        value: tp.Optional[tp.Dict[str, tp.Any]] = None,
        status: str = "ACTIVE",
        **kwargs,
    ) -> sys_uuid.UUID:
        from gcl_sdk.agents.universal.dm import models as ua_models

        uuid = uuid or sys_uuid.uuid4()
        value = value or {"key": "value"}
        res_uuid = ua_models.Resource.gen_res_uuid(uuid, kind)
        resource = ua_models.Resource(
            uuid=uuid,
            kind=kind,
            res_uuid=res_uuid,
            value=value,
            status=status,
            **kwargs,
        )
        resource.insert()
        return res_uuid

    def test_admin_list_resources(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        resource_uuid = self._resource_factory()
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["ua", "resources"])

        response = client.get(url)
        output = response.json()

        assert response.status_code == 200
        uuids = [item["res_uuid"] for item in output]
        assert str(resource_uuid) in uuids

    def test_admin_list_resources_empty(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["ua", "resources"])

        response = client.get(url)

        assert response.status_code == 200
        assert len(response.json()) == 0

    def test_admin_get_resource(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        resource_uuid = self._resource_factory()
        client = user_api_client(auth_user_admin)
        url = client.build_resource_uri(["ua", "resources", str(resource_uuid)])

        response = client.get(url)
        output = response.json()

        assert response.status_code == 200
        assert output["res_uuid"] == str(resource_uuid)

    def test_admin_get_resource_not_found(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        url = client.build_resource_uri(
            ["ua", "resources", "00000000-0000-0000-0000-000000000000"]
        )

        with pytest.raises(bazooka_exc.NotFoundError):
            client.get(url)

    def test_nonadmin_no_access(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_test1_user: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_test1_user)
        url = client.build_collection_uri(["ua", "resources"])

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.get(url)

    def test_unauthorized_no_access(
        self,
        user_api_noauth_client: tp.Callable,
    ):
        client = user_api_noauth_client()
        url = client.build_collection_uri(["ua", "resources"])

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.get(url)

    def test_user_with_permission_can_list(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_test1_user: iam_clients.GenesisCoreAuth,
    ):
        resource_uuid = self._resource_factory()
        client = user_api_client(
            auth_test1_user,
            permissions=[
                iam_c.PERMISSION_WILDCARD_NAME,
            ],
        )
        url = client.build_collection_uri(["ua", "resources"])

        response = client.get(url)
        output = response.json()

        assert response.status_code == 200
        uuids = [item["res_uuid"] for item in output]
        assert str(resource_uuid) in uuids


class TestUaTargetResourcesApi:
    @staticmethod
    def _target_resource_factory(
        uuid: tp.Optional[sys_uuid.UUID] = None,
        kind: str = "test_target_kind",
        value: tp.Optional[tp.Dict[str, tp.Any]] = None,
        status: str = "ACTIVE",
        **kwargs,
    ) -> sys_uuid.UUID:
        from gcl_sdk.agents.universal.dm import models as ua_models

        uuid = uuid or sys_uuid.uuid4()
        value = value or {"key": "value"}
        res_uuid = ua_models.TargetResource.gen_res_uuid(uuid, kind)
        target = ua_models.TargetResource(
            uuid=uuid,
            kind=kind,
            res_uuid=res_uuid,
            value=value,
            status=status,
            **kwargs,
        )
        target.insert()
        return res_uuid

    def test_admin_list_target_resources(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        target_uuid = self._target_resource_factory()
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["ua", "target_resources"])

        response = client.get(url)
        output = response.json()

        assert response.status_code == 200
        uuids = [item["res_uuid"] for item in output]
        assert str(target_uuid) in uuids

    def test_admin_list_target_resources_empty(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["ua", "target_resources"])

        response = client.get(url)

        assert response.status_code == 200
        assert len(response.json()) == 0

    def test_admin_get_target_resource(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        target_uuid = self._target_resource_factory()
        client = user_api_client(auth_user_admin)
        url = client.build_resource_uri(["ua", "target_resources", str(target_uuid)])

        response = client.get(url)
        output = response.json()

        assert response.status_code == 200
        assert output["res_uuid"] == str(target_uuid)

    def test_admin_get_target_resource_not_found(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        url = client.build_resource_uri(
            ["ua", "target_resources", "00000000-0000-0000-0000-000000000000"]
        )

        with pytest.raises(bazooka_exc.NotFoundError):
            client.get(url)

    def test_nonadmin_no_access(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_test1_user: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_test1_user)
        url = client.build_collection_uri(["ua", "target_resources"])

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.get(url)

    def test_unauthorized_no_access(
        self,
        user_api_noauth_client: tp.Callable,
    ):
        client = user_api_noauth_client()
        url = client.build_collection_uri(["ua", "target_resources"])

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.get(url)

    def test_user_with_permission_can_list(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_test1_user: iam_clients.GenesisCoreAuth,
    ):
        target_uuid = self._target_resource_factory()
        client = user_api_client(
            auth_test1_user,
            permissions=[
                iam_c.PERMISSION_WILDCARD_NAME,
            ],
        )
        url = client.build_collection_uri(["ua", "target_resources"])

        response = client.get(url)
        output = response.json()

        assert response.status_code == 200
        uuids = [item["res_uuid"] for item in output]
        assert str(target_uuid) in uuids
