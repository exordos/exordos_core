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

import os

from gcl_iam.tests.functional import clients as iam_clients
import yaml

from exordos_core.common.utils import PROJECT_PATH


class TestEmUserApi:
    def test_schema(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["em", "manifests", "schema"])

        response = client.get(url)
        assert response.status_code == 200
        assert isinstance(response.json(), dict)

    def test_manifests(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):

        client = user_api_client(auth_user_admin)
        url = client.build_collection_uri(["em", "elements"])

        response = client.get(url)
        assert response.status_code == 200
        assert isinstance(response.json(), list)
        assert len(response.json()) == 0

        with open(
            os.path.join(
                PROJECT_PATH, "exordos", "manifests", "examples", "core.element.yaml"
            ),
            "r",
        ) as f:
            manifest = yaml.safe_load(f)

        url = client.build_collection_uri(["em", "manifests"])
        response = client.post(url, json=manifest)
        assert response.status_code == 201

        manifest_data = response.json()
        manifest_uuid = manifest_data["uuid"]

        url = client.build_resource_uri(
            ["em", "manifests", manifest_uuid, "actions", "validate"]
        )
        response = client.get(url)
        assert response.status_code == 200

        url = client.build_resource_uri(
            ["em", "manifests", manifest_uuid, "actions", "install", "invoke"]
        )
        response = client.post(url)
        assert response.status_code == 200

        url = client.build_collection_uri(["em", "elements"])

        response = client.get(url)
        assert response.status_code == 200
        element_data = response.json()
        assert len(element_data) == 1
        element_uuid = element_data[0]["uuid"]

        url = client.build_collection_uri(["em", "elements", element_uuid, "exports"])
        response = client.get(url)
        assert response.status_code == 200

        exports_data = response.json()
        assert isinstance(exports_data, list)
        assert len(element_data) > 0

        url = client.build_collection_uri(["em", "elements", element_uuid, "imports"])
        response = client.get(url)
        assert response.status_code == 200

        url = client.build_collection_uri(["em", "elements", element_uuid, "resources"])
        response = client.get(url)
        assert response.status_code == 200
        resources_data = response.json()
        assert isinstance(resources_data, list)

        url = client.build_collection_uri(["em", "resources"])
        response = client.get(url)
        assert response.status_code == 200
        resources_data = response.json()
        assert isinstance(resources_data, list)

        url = client.build_collection_uri(["em", "imports"])
        response = client.get(url)
        assert response.status_code == 200
        imports_data = response.json()
        assert isinstance(imports_data, list)

        url = client.build_collection_uri(["em", "exports"])
        response = client.get(url)
        assert response.status_code == 200
        exports_data = response.json()
        assert isinstance(exports_data, list)

        # uninstall
        url = client.build_resource_uri(
            ["em", "manifests", manifest_uuid, "actions", "uninstall", "invoke"]
        )
        response = client.post(url)
        assert response.status_code == 200

        url = client.build_collection_uri(["em", "elements"])

        response = client.get(url)
        assert response.status_code == 200
        element_data = response.json()
        assert len(element_data) == 0

        # delete manifest
        url = client.build_resource_uri(["em", "manifests", manifest_uuid])
        response = client.delete(url)
        assert response.status_code == 204

        url = client.build_collection_uri(["em", "manifests"])

        response = client.get(url)
        assert response.status_code == 200
        manifest_data = response.json()
        assert len(manifest_data) == 0
