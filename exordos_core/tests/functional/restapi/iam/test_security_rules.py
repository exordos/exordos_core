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

import uuid as sys_uuid

from bazooka import exceptions as bazooka_exc
from gcl_iam.tests.functional import clients as iam_clients
import pytest

from exordos_core.user_api.security.dm import models as security_models


class TestSecurityRules:
    @staticmethod
    def _create_block_email_rule(user_uuid) -> security_models.Rule:
        rule = security_models.Rule(
            name="Block email update",
            condition=security_models.UriConditions(
                uri=f"/v1/iam/users/{user_uuid}",
                method="PUT",
            ),
            action=security_models.FieldNotInRequestVerifier(
                fields=["email"],
            ),
            operator=security_models.OperatorEnum.AND.value,
        )
        rule.insert()
        return rule

    @staticmethod
    def _create_block_field_rule(user_uuid, fields, project_id=None):
        if isinstance(project_id, str):
            project_id = sys_uuid.UUID(project_id)
        rule = security_models.Rule(
            name="Block fields update",
            condition=security_models.UriConditions(
                uri=f"/v1/iam/users/{user_uuid}",
                method="PUT",
            ),
            action=security_models.FieldNotInRequestVerifier(
                fields=fields,
            ),
            operator=security_models.OperatorEnum.OR.value,
            project_id=project_id,
        )
        rule.insert()
        return rule

    @staticmethod
    def _create_or_block_email_rule(user_uuid):
        rule = security_models.Rule(
            name="Block email update (OR)",
            condition=security_models.UriConditions(
                uri=f"/v1/iam/users/{user_uuid}",
                method="PUT",
            ),
            action=security_models.FieldNotInRequestVerifier(
                fields=["email"],
            ),
            operator=security_models.OperatorEnum.OR.value,
        )
        rule.insert()
        return rule

    @staticmethod
    def _build_rule_payload(name, user_uuid):
        return {
            "name": name,
            "condition": {
                "kind": "uri",
                "uri": f"/v1/iam/users/{user_uuid}",
                "method": "PUT",
            },
            "action": {
                "kind": "no_fields",
                "fields": ["email"],
            },
            "operator": security_models.OperatorEnum.AND.value,
        }

    @staticmethod
    def _create_rule_via_api(client, user_uuid, name):
        rule_payload = TestSecurityRules._build_rule_payload(
            name=name,
            user_uuid=user_uuid,
        )
        collection_url = client.build_collection_uri(["security/rules"])
        return client.post(collection_url, json=rule_payload).json()

    def test_email_update_blocked(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_test1_user: iam_clients.GenesisCoreAuth,
    ):
        rule = self._create_block_email_rule(auth_test1_user.uuid)
        client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.update_user(
                auth_test1_user.uuid,
                email="new-email@example.com",
            )

        rule.delete()

    def test_project_rule_applied_when_project_in_context(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_test1_p1_user: iam_clients.GenesisCoreAuth,
    ):
        self._create_block_field_rule(
            user_uuid=auth_test1_p1_user.uuid,
            fields=["first_name"],
            project_id=auth_test1_p1_user.project_id,
        )
        client = user_api_client(auth_test1_p1_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.update_user(
                auth_test1_p1_user.uuid,
                first_name="Updated",
            )

    def test_project_rule_ignored_without_project_in_context(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_test1_p1_user: iam_clients.GenesisCoreAuth,
        auth_test1_user: iam_clients.GenesisCoreAuth,
    ):
        self._create_block_field_rule(
            user_uuid=auth_test1_user.uuid,
            fields=["first_name"],
            project_id=auth_test1_p1_user.project_id,
        )
        client = user_api_client(auth_test1_user)

        response = client.update_user(
            auth_test1_user.uuid,
            first_name="Updated",
        )

        assert response["first_name"] == "Updated"

    def test_non_email_update_allowed(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_test1_user: iam_clients.GenesisCoreAuth,
    ):
        rule = self._create_block_email_rule(auth_test1_user.uuid)
        client = user_api_client(auth_test1_user)

        response = client.update_user(
            auth_test1_user.uuid,
            first_name="Updated",
        )

        assert response["first_name"] == "Updated"

        rule.delete()

    def test_or_only_rules_are_evaluated(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_test1_user: iam_clients.GenesisCoreAuth,
    ):
        rule = self._create_or_block_email_rule(auth_test1_user.uuid)
        client = user_api_client(auth_test1_user)

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.update_user(
                auth_test1_user.uuid,
                email="new-email@example.com",
            )

        rule.delete()

    def test_rules_create_success(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(
            auth_user_admin,
            permissions=["security.rule.create"],
        )

        created = self._create_rule_via_api(
            client=client,
            user_uuid=auth_user_admin.uuid,
            name="Create rule",
        )

        assert created["name"] == "Create rule"

        rule_obj = security_models.Rule.objects.get_one(
            filters={"uuid": created["uuid"]}
        )
        rule_obj.delete()

    def test_rules_read_success(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(
            auth_user_admin,
            permissions=["security.rule.create", "security.rule.read"],
        )

        created = self._create_rule_via_api(
            client=client,
            user_uuid=auth_user_admin.uuid,
            name="Read rule",
        )

        resource_url = client.build_resource_uri(["security/rules/", created["uuid"]])
        fetched = client.get(resource_url).json()

        assert fetched["uuid"] == created["uuid"]

    def test_rules_update_success(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(
            auth_user_admin,
            permissions=["security.rule.create", "security.rule.update"],
        )

        created = self._create_rule_via_api(
            client=client,
            user_uuid=auth_user_admin.uuid,
            name="Update rule",
        )

        resource_url = client.build_resource_uri(["security/rules/", created["uuid"]])
        updated = client.put(
            resource_url,
            json={"name": "Update rule updated"},
        ).json()

        assert updated["name"] == "Update rule updated"

    def test_rules_delete_success(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(
            auth_user_admin,
            permissions=["security.rule.create", "security.rule.delete"],
        )

        created = self._create_rule_via_api(
            client=client,
            user_uuid=auth_user_admin.uuid,
            name="Delete rule",
        )

        resource_url = client.build_resource_uri(["security/rules/", created["uuid"]])
        delete_response = client.delete(resource_url)

        assert delete_response.status_code == 204

    def test_rules_create_forbidden_without_permissions(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_test1_user: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(auth_test1_user)
        rule_payload = self._build_rule_payload(
            name="Forbidden rule",
            user_uuid=auth_test1_user.uuid,
        )
        collection_url = client.build_collection_uri(["security/rules"])

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.post(collection_url, json=rule_payload)

    def test_rules_read_forbidden_without_permissions(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        auth_test1_user: iam_clients.GenesisCoreAuth,
    ):
        admin_client = user_api_client(
            auth_user_admin,
            permissions=["security.rule.create", "security.rule.read"],
        )
        created = self._create_rule_via_api(
            client=admin_client,
            user_uuid=auth_user_admin.uuid,
            name="Read forbidden rule",
        )

        client = user_api_client(auth_test1_user)
        resource_url = client.build_resource_uri(["security/rules/", created["uuid"]])

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.get(resource_url)

    def test_rules_update_forbidden_without_permissions(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        auth_test1_user: iam_clients.GenesisCoreAuth,
    ):
        admin_client = user_api_client(
            auth_user_admin,
            permissions=["security.rule.create", "security.rule.update"],
        )
        created = self._create_rule_via_api(
            client=admin_client,
            user_uuid=auth_user_admin.uuid,
            name="Update forbidden rule",
        )

        client = user_api_client(auth_test1_user)
        resource_url = client.build_resource_uri(["security/rules/", created["uuid"]])

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.put(resource_url, json={"name": "Denied"})

    def test_rules_delete_forbidden_without_permissions(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_user_admin: iam_clients.GenesisCoreAuth,
        auth_test1_user: iam_clients.GenesisCoreAuth,
    ):
        admin_client = user_api_client(
            auth_user_admin,
            permissions=["security.rule.create", "security.rule.delete"],
        )
        created = self._create_rule_via_api(
            client=admin_client,
            user_uuid=auth_user_admin.uuid,
            name="Delete forbidden rule",
        )

        client = user_api_client(auth_test1_user)
        resource_url = client.build_resource_uri(["security/rules/", created["uuid"]])

        with pytest.raises(bazooka_exc.ForbiddenError):
            client.delete(resource_url)

        admin_resource_url = admin_client.build_resource_uri(
            ["security/rules/", created["uuid"]]
        )
        admin_client.delete(admin_resource_url)

    def test_rules_project_inherited_from_context(
        self,
        user_api_client: iam_clients.GenesisCoreTestRESTClient,
        auth_test1_p1_user: iam_clients.GenesisCoreAuth,
    ):
        client = user_api_client(
            auth_test1_p1_user,
            permissions=["security.rule.create", "security.rule.read"],
            project_id=auth_test1_p1_user.project_id,
        )
        rule_payload = self._build_rule_payload(
            name="Project rule",
            user_uuid=auth_test1_p1_user.uuid,
        )
        collection_url = client.build_collection_uri(["security/rules"])

        created = client.post(collection_url, json=rule_payload).json()

        assert created["project_id"] == auth_test1_p1_user.project_id

        rule_obj = security_models.Rule.objects.get_one(
            filters={"uuid": created["uuid"]}
        )
        rule_obj.delete()
