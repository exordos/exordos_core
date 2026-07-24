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
import uuid as sys_uuid

from bazooka import exceptions as bazooka_exc
from gcl_iam.tests.functional import clients as iam_clients
import pytest

from exordos_core.common import constants as c
from exordos_core.quota.dm.models import QuotaLimit

_API_PREFIX = ["quota"]


@pytest.fixture
def project_id():
    return sys_uuid.uuid4()


@pytest.fixture
def quota_limit_for_project(project_id):
    quota_limit = QuotaLimit(
        uuid=sys_uuid.uuid4(),
        project_id=project_id,
        resource_name="net_lb",
        limit=10,
    )
    quota_limit.insert()
    yield quota_limit
    try:
        quota_limit.delete()
    except Exception:
        pass


class TestQuotaLimitsUserApi:
    @staticmethod
    def _limit_cmp_shallow(
        a: tp.Dict[str, tp.Any],
        b: tp.Dict[str, tp.Any],
    ) -> bool:
        return all(
            a.get(key, "") == b[key]
            for key in (
                "uuid",
                "project_id",
                "resource_name",
                "field_name",
                "limit",
            )
        )

    def test_limits_list(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(_API_PREFIX + ["limits"])

        response = client.get(url)

        assert response.status_code == 200
        assert len(response.json()) == 0

    def test_limits_add(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(_API_PREFIX + ["limits"])

        limit_data = {
            "uuid": str(sys_uuid.uuid4()),
            "project_id": str(c.SERVICE_PROJECT_ID),
            "resource_name": "nodes",
            "field_name": "cores",
            "limit": 5,
        }
        response = client.post(url, json=limit_data)
        output = response.json()

        assert response.status_code == 201
        assert self._limit_cmp_shallow(limit_data, output)

        url = client.build_resource_uri(_API_PREFIX + ["limits", output["uuid"]])
        client.delete(url)

    def test_limits_add_several(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(_API_PREFIX + ["limits"])
        urls = []

        for i in range(3):
            limit_data = {
                "uuid": str(sys_uuid.uuid4()),
                "project_id": str(c.SERVICE_PROJECT_ID),
                "resource_name": f"test_resource_{i}",
                "limit": i + 1,
            }
            response = client.post(url, json=limit_data)
            output = response.json()

            assert response.status_code == 201
            assert self._limit_cmp_shallow(limit_data, output)
            urls.append(
                client.build_resource_uri(_API_PREFIX + ["limits", output["uuid"]])
            )

        for u in urls:
            response = client.get(u)
            assert response.status_code == 200

        for u in urls:
            client.delete(u)

    def test_limits_get(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(_API_PREFIX + ["limits"])

        limit_data = {
            "uuid": str(sys_uuid.uuid4()),
            "project_id": str(c.SERVICE_PROJECT_ID),
            "resource_name": "test_resource",
            "limit": 5,
        }
        response = client.post(url, json=limit_data)
        output = response.json()
        assert response.status_code == 201

        url = client.build_resource_uri(_API_PREFIX + ["limits", output["uuid"]])
        response = client.get(url)
        assert response.status_code == 200
        assert self._limit_cmp_shallow(limit_data, response.json())

        client.delete(url)

    def test_limits_update(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(_API_PREFIX + ["limits"])

        limit_data = {
            "uuid": str(sys_uuid.uuid4()),
            "project_id": str(c.SERVICE_PROJECT_ID),
            "resource_name": "test_resource",
            "limit": 5,
        }
        response = client.post(url, json=limit_data)
        output = response.json()
        assert response.status_code == 201

        update = {"limit": 10}
        url = client.build_resource_uri(_API_PREFIX + ["limits", output["uuid"]])
        response = client.put(url, json=update)
        output = response.json()

        assert response.status_code == 200
        assert output["limit"] == 10
        assert output["resource_name"] == "test_resource"

        client.delete(url)

    def test_limits_delete(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(_API_PREFIX + ["limits"])

        limit_data = {
            "uuid": str(sys_uuid.uuid4()),
            "project_id": str(c.SERVICE_PROJECT_ID),
            "resource_name": "test_resource",
            "limit": 5,
        }
        response = client.post(url, json=limit_data)
        output = response.json()
        assert response.status_code == 201

        url = client.build_resource_uri(_API_PREFIX + ["limits", output["uuid"]])
        response = client.delete(url)
        assert response.status_code == 204

        with pytest.raises(bazooka_exc.NotFoundError):
            client.get(url)
