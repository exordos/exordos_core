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
from gcl_sdk.agents.universal.api.packers import EXORDOS_NODE_UUID_HEADER
import ruamel.yaml

from exordos_core.common.utils import PROJECT_PATH

SPECIFICATIONS_PATH = "specifications/3.0.3"
yaml = ruamel.yaml.YAML()
yaml.indent(sequence=4, offset=2)


class TestGetOpenApiSpecs:
    def test_user_openapi(
        self,
        user_api,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        boot_api_service,
        boot_api_noauth_client: iam_clients.GenesisCoreTestNoAuthRESTClient,
        orch_api_service,
        orch_api_noauth_client: iam_clients.GenesisCoreTestNoAuthRESTClient,
        status_api_service,
        status_api_noauth_client: iam_clients.GenesisCoreTestNoAuthRESTClient,
        default_node,
    ):
        # User API
        client = user_api_client(auth_user_admin)
        url = f"{user_api.get_endpoint()}{SPECIFICATIONS_PATH}"
        response = client.get(url)
        assert response.status_code == 200

        path = os.path.join(PROJECT_PATH, "docs", "openapi", "openapi_user.yaml")
        spec = response.json()
        spec["servers"][0]["url"] = "http://127.0.0.1:11010"
        spec["info"]["version"] = "latest"
        with open(path, "w") as f:
            yaml.dump(spec, f)

        # Boot API
        url = f"{boot_api_service.get_endpoint()}{SPECIFICATIONS_PATH}"
        response = boot_api_noauth_client.get(
            url,
        )
        assert response.status_code == 200
        path = os.path.join(PROJECT_PATH, "docs", "openapi", "openapi_boot.yaml")
        spec = response.json()
        spec["servers"][0]["url"] = "http://127.0.0.1:11013"
        spec["info"]["version"] = "latest"
        with open(path, "w") as f:
            yaml.dump(spec, f)

        # Orch API
        url = f"{orch_api_service.get_endpoint()}{SPECIFICATIONS_PATH}"
        response = orch_api_noauth_client.get(
            url,
            headers={
                EXORDOS_NODE_UUID_HEADER: default_node["uuid"],
                "Content-Type": "application/x-genesis-agent-chacha20-poly1305-encrypted",
            },
        )
        assert response.status_code == 200

        path = os.path.join(PROJECT_PATH, "docs", "openapi", "openapi_orch.yaml")
        spec = response.json()
        spec["servers"][0]["url"] = "http://127.0.0.1:11011"
        spec["info"]["version"] = "latest"
        with open(path, "w") as f:
            yaml.dump(spec, f)

        # Status API
        url = f"{status_api_service.get_endpoint()}{SPECIFICATIONS_PATH}"
        response = status_api_noauth_client.get(
            url,
            headers={
                EXORDOS_NODE_UUID_HEADER: default_node["uuid"],
                "Content-Type": "application/x-genesis-agent-chacha20-poly1305-encrypted",
            },
        )
        assert response.status_code == 200

        path = os.path.join(PROJECT_PATH, "docs", "openapi", "openapi_status.yaml")
        spec = response.json()
        spec["servers"][0]["url"] = "http://127.0.0.1:11012"
        spec["info"]["version"] = "latest"
        with open(path, "w") as f:
            yaml.dump(spec, f)
