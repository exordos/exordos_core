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

from gcl_iam.tests.functional import clients as iam_clients


class TestAuditApi:
    def test_node_audit_lifecycle(
        self,
        node_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_user_admin)
        node = node_factory()
        node_url = client.build_collection_uri(["compute", "nodes"])

        response = client.post(node_url, json=node)
        assert response.status_code == 201

        resource_url = client.build_resource_uri(
            ["compute", "nodes", node["uuid"]]
        )
        response = client.put(resource_url, json={"cores": 2, "ram": 2048})
        assert response.status_code == 200

        response = client.delete(resource_url)
        assert response.status_code == 204

        response = client.get(
            client.build_collection_uri(["security", "audit"]),
            params={"resource_uuid": node["uuid"]},
        )
        assert response.status_code == 200

        events = response.json()
        assert len(events) == 3
        assert {event["action"] for event in events} == {
            "create",
            "update",
            "delete",
        }

        events_by_action = {event["action"]: event for event in events}
        for event in events:
            assert event["service_name"] == "compute"
            assert event["resource_type"] == "node"
            assert event["resource_uuid"] == node["uuid"]
            assert event["project_id"] == node["project_id"]
            assert event["actor_user_uuid"] == auth_user_admin.uuid

        assert events_by_action["create"]["snapshot"]["cores"] == 1
        assert events_by_action["update"]["snapshot"]["cores"] == 2
        assert events_by_action["update"]["snapshot"]["ram"] == 2048
        assert events_by_action["delete"]["snapshot"] is None

    def test_audit_events_are_project_scoped(
        self,
        node_factory: tp.Callable,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        auth_test1_p1_user: iam_clients.GenesisCoreAuth,
        auth_test2_p1_user: iam_clients.GenesisCoreAuth,
    ):
        admin_client = user_api_client(auth_user_admin)
        nodes_url = admin_client.build_collection_uri(["compute", "nodes"])
        node_a = node_factory(project_id=sys_uuid.UUID(auth_test1_p1_user.project_id))
        node_b = node_factory(project_id=sys_uuid.UUID(auth_test2_p1_user.project_id))

        assert admin_client.post(nodes_url, json=node_a).status_code == 201
        assert admin_client.post(nodes_url, json=node_b).status_code == 201

        project_client = user_api_client(
            auth_test1_p1_user,
            permissions=["audit.events.read"],
            project_id=auth_test1_p1_user.project_id,
        )
        response = project_client.get(
            project_client.build_collection_uri(["security", "audit"]),
        )

        assert response.status_code == 200
        assert {event["resource_uuid"] for event in response.json()} == {
            node_a["uuid"]
        }

        for node in (node_a, node_b):
            admin_client.delete(
                admin_client.build_resource_uri(
                    ["compute", "nodes", node["uuid"]]
                )
            )
