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

from exordos_core.common import constants as c


class TestVSUserApi:
    @staticmethod
    def _profile_factory(
        uuid: tp.Optional[sys_uuid.UUID] = None,
        name: tp.Optional[str] = None,
        description: str = "test profile",
        project_id: sys_uuid.UUID = c.SERVICE_PROJECT_ID,
        profile_type: str = "GLOBAL",
        **kwargs,
    ) -> tp.Dict[str, tp.Any]:
        uuid = uuid or sys_uuid.uuid4()
        name = name or f"profile_{str(uuid)[:8]}"
        return {
            "uuid": str(uuid),
            "name": name,
            "description": description,
            "project_id": str(project_id),
            "profile_type": profile_type,
            **kwargs,
        }

    @staticmethod
    def _variable_factory(
        uuid: tp.Optional[sys_uuid.UUID] = None,
        name: tp.Optional[str] = None,
        description: str = "test variable",
        project_id: sys_uuid.UUID = c.SERVICE_PROJECT_ID,
        setter: tp.Optional[tp.Dict[str, tp.Any]] = None,
        **kwargs,
    ) -> tp.Dict[str, tp.Any]:
        uuid = uuid or sys_uuid.uuid4()
        name = name or f"var_{str(uuid)[:8]}"
        if setter is None:
            setter = {"kind": "selector", "selector_strategy": "latest"}
        return {
            "uuid": str(uuid),
            "name": name,
            "description": description,
            "project_id": str(project_id),
            "setter": setter,
            **kwargs,
        }

    @staticmethod
    def _value_factory(
        uuid: tp.Optional[sys_uuid.UUID] = None,
        name: tp.Optional[str] = None,
        description: str = "test value",
        project_id: sys_uuid.UUID = c.SERVICE_PROJECT_ID,
        value: tp.Any = 1,
        variable: tp.Optional[str] = None,
        **kwargs,
    ) -> tp.Dict[str, tp.Any]:
        uuid = uuid or sys_uuid.uuid4()
        name = name or f"value_{str(uuid)[:8]}"
        payload: tp.Dict[str, tp.Any] = {
            "uuid": str(uuid),
            "name": name,
            "description": description,
            "project_id": str(project_id),
            "value": value,
            **kwargs,
        }
        if variable is not None:
            payload["variable"] = variable
        return payload

    def test_version(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri([])

        response = client.get(url)
        assert response.status_code == 200

    def test_profiles_create(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        profile = self._profile_factory(profile_type="GLOBAL")
        url = client.build_collection_uri(["vs", "profiles"])

        response = client.post(url, json=profile)
        output = response.json()
        assert response.status_code == 201
        assert output["uuid"] == profile["uuid"]
        assert output["name"] == profile["name"]

    def test_profiles_update(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        profile = self._profile_factory(profile_type="GLOBAL")
        url = client.build_collection_uri(["vs", "profiles"])

        response = client.post(url, json=profile)
        assert response.status_code == 201

        update = {"description": "updated profile"}
        url = client.build_resource_uri(["vs", "profiles", profile["uuid"]])
        response = client.put(url, json=update)
        output = response.json()
        assert response.status_code == 200
        assert output["description"] == "updated profile"

    def test_profiles_delete(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        profile = self._profile_factory(profile_type="GLOBAL")
        url = client.build_collection_uri(["vs", "profiles"])

        response = client.post(url, json=profile)
        assert response.status_code == 201

        url = client.build_resource_uri(["vs", "profiles", profile["uuid"]])

        response = client.delete(url)
        assert response.status_code == 204

        with pytest.raises(bazooka_exc.NotFoundError):
            client.get(url)

    def test_profiles_activate_global_profile(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["vs", "profiles"])

        profile1 = self._profile_factory(profile_type="GLOBAL")
        response = client.post(url, json=profile1)
        assert response.status_code == 201

        profile2 = self._profile_factory(profile_type="GLOBAL")
        response = client.post(url, json=profile2)
        assert response.status_code == 201

        url = client.build_resource_uri(
            [
                "vs",
                "profiles",
                profile1["uuid"],
                "actions",
                "activate",
                "invoke",
            ]
        )
        response = client.post(url, json={})
        output = response.json()
        assert response.status_code == 200
        assert output["uuid"] == profile1["uuid"]
        assert output["active"] is True

        url = client.build_resource_uri(["vs", "profiles", profile2["uuid"]])
        response = client.get(url)
        output = response.json()
        assert response.status_code == 200
        assert output["uuid"] == profile2["uuid"]
        assert output["active"] is False

        url = client.build_resource_uri(
            [
                "vs",
                "profiles",
                profile2["uuid"],
                "actions",
                "activate",
                "invoke",
            ]
        )
        response = client.post(url, json={})
        output = response.json()
        assert response.status_code == 200
        assert output["uuid"] == profile2["uuid"]
        assert output["active"] is True

        url = client.build_resource_uri(["vs", "profiles", profile1["uuid"]])
        response = client.get(url)
        output = response.json()
        assert response.status_code == 200
        assert output["uuid"] == profile1["uuid"]
        assert output["active"] is False

    def test_variables_create(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        variable = self._variable_factory()
        url = client.build_collection_uri(["vs", "variables"])

        response = client.post(url, json=variable)
        output = response.json()
        assert response.status_code == 201
        assert output["uuid"] == variable["uuid"]
        assert output["name"] == variable["name"]
        client.delete(client.build_resource_uri(["vs", "variables", variable["uuid"]]))

    def test_variables_update(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        variable = self._variable_factory()
        url = client.build_collection_uri(["vs", "variables"])

        response = client.post(url, json=variable)
        assert response.status_code == 201

        update = {"description": "updated variable"}
        url = client.build_resource_uri(["vs", "variables", variable["uuid"]])
        response = client.put(url, json=update)
        output = response.json()
        assert response.status_code == 200
        assert output["description"] == "updated variable"
        assert output["status"] == "IN_PROGRESS"

    def test_variables_delete(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        variable = self._variable_factory()
        url = client.build_collection_uri(["vs", "variables"])

        response = client.post(url, json=variable)
        assert response.status_code == 201

        url = client.build_resource_uri(["vs", "variables", variable["uuid"]])

        response = client.delete(url)
        assert response.status_code == 204

        with pytest.raises(bazooka_exc.NotFoundError):
            client.get(url)

    def test_variables_select_value_action(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        variable = self._variable_factory(
            setter={"kind": "selector", "selector_strategy": "latest"}
        )
        url = client.build_collection_uri(["vs", "variables"])
        response = client.post(url, json=variable)
        assert response.status_code == 201

        value1 = self._value_factory(
            value=1,
            variable=f"/v1/vs/variables/{variable['uuid']}",
        )
        value2 = self._value_factory(
            value=2,
            variable=f"/v1/vs/variables/{variable['uuid']}",
        )
        url = client.build_collection_uri(["vs", "values"])
        response = client.post(url, json=value1)
        assert response.status_code == 201
        response = client.post(url, json=value2)
        assert response.status_code == 201

        url = client.build_resource_uri(
            [
                "vs",
                "variables",
                variable["uuid"],
                "actions",
                "select_value",
                "invoke",
            ]
        )
        response = client.post(url, json={"value": value1["uuid"]})
        output = response.json()
        assert response.status_code == 200

        url = client.build_resource_uri(["vs", "values", value1["uuid"]])
        response = client.get(url)
        output = response.json()
        assert response.status_code == 200
        assert output["manual_selected"] is True

        url = client.build_resource_uri(["vs", "values", value2["uuid"]])
        response = client.get(url)
        output = response.json()
        assert response.status_code == 200
        assert output["manual_selected"] is False

    def test_variables_release_value_action(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        variable = self._variable_factory(
            setter={"kind": "selector", "selector_strategy": "latest"}
        )
        url = client.build_collection_uri(["vs", "variables"])
        response = client.post(url, json=variable)
        assert response.status_code == 201

        value1 = self._value_factory(
            value=1,
            variable=f"/v1/vs/variables/{variable['uuid']}",
        )
        value2 = self._value_factory(
            value=2,
            variable=f"/v1/vs/variables/{variable['uuid']}",
        )
        url = client.build_collection_uri(["vs", "values"])
        response = client.post(url, json=value1)
        assert response.status_code == 201
        response = client.post(url, json=value2)
        assert response.status_code == 201

        url = client.build_resource_uri(
            [
                "vs",
                "variables",
                variable["uuid"],
                "actions",
                "select_value",
                "invoke",
            ]
        )
        response = client.post(url, json={"value": value1["uuid"]})
        assert response.status_code == 200

        url = client.build_resource_uri(
            [
                "vs",
                "variables",
                variable["uuid"],
                "actions",
                "release_value",
                "invoke",
            ]
        )
        response = client.post(url, json={})
        assert response.status_code == 200

        url = client.build_resource_uri(["vs", "values", value1["uuid"]])
        response = client.get(url)
        output = response.json()
        assert response.status_code == 200
        assert output["manual_selected"] is False

        url = client.build_resource_uri(["vs", "values", value2["uuid"]])
        response = client.get(url)
        output = response.json()
        assert response.status_code == 200
        assert output["manual_selected"] is False

    def test_values_create(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        value = self._value_factory(value=1)
        url = client.build_collection_uri(["vs", "values"])
        response = client.post(url, json=value)
        output = response.json()
        assert response.status_code == 201
        assert output["uuid"] == value["uuid"]
        assert output["value"] == 1

    def test_values_create_with_value(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        variable = self._variable_factory()
        url = client.build_collection_uri(["vs", "variables"])
        response = client.post(url, json=variable)
        assert response.status_code == 201

        value = self._value_factory(
            value=1, variable=f"/v1/vs/variables/{variable['uuid']}"
        )
        url = client.build_collection_uri(["vs", "values"])
        response = client.post(url, json=value)
        output = response.json()
        assert response.status_code == 201
        assert output["uuid"] == value["uuid"]
        assert output["value"] == 1
        client.delete(client.build_resource_uri(["vs", "values", value["uuid"]]))
        client.delete(client.build_resource_uri(["vs", "variables", variable["uuid"]]))

    def test_values_update(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        value = self._value_factory(value=1)
        url = client.build_collection_uri(["vs", "values"])
        response = client.post(url, json=value)
        assert response.status_code == 201

        update = {"value": 2}
        url = client.build_resource_uri(["vs", "values", value["uuid"]])
        response = client.put(url, json=update)
        output = response.json()
        assert response.status_code == 200
        assert output["value"] == 2

    def test_values_delete(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)

        value = self._value_factory(value=1)
        url = client.build_collection_uri(["vs", "values"])
        response = client.post(url, json=value)
        assert response.status_code == 201

        url = client.build_resource_uri(["vs", "values", value["uuid"]])

        response = client.delete(url)
        assert response.status_code == 204

        with pytest.raises(bazooka_exc.NotFoundError):
            client.get(url)
