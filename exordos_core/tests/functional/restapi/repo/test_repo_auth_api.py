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

"""nginx asks /v1/repo/auth/ about every /repo/<project_id>/ request and
passes it only on 2xx: reads from the realm need no token, everything else
needs a token of that very project."""

import uuid as sys_uuid

from bazooka import exceptions as bazooka_exc
import pytest
from restalchemy.dm import filters as dm_filters

from exordos_core.common import constants as c
from exordos_core.repo import internal
from exordos_core.repo.dm import models as repo_models
from exordos_core.vs.dm import models as vs_models

CORE_IP = "10.20.0.2"
REALM_ADDR = "127.0.0.1"
OUTSIDE_ADDR = "203.0.113.5"


@pytest.fixture
def core_ip():
    if not vs_models.Value.objects.get_one_or_none(
        filters={"uuid": dm_filters.EQ(c.VALUE_CORE_IP_ADDRESS_UUID)}
    ):
        vs_models.Value(
            uuid=c.VALUE_CORE_IP_ADDRESS_UUID,
            value=CORE_IP,
            project_id=c.EM_HIDDEN_PROJECT_ID,
        ).insert()
    return CORE_IP


def _auth_status(client, method, uri, addr=OUTSIDE_ADDR):
    url = client.build_collection_uri(["repo", "auth"])
    headers = {
        "X-Original-Method": method,
        "X-Original-URI": uri,
        "X-Original-Addr": addr,
    }
    try:
        return client.get(url, headers=headers).status_code
    except bazooka_exc.BaseHTTPException as e:
        return e.cause.response.status_code


@pytest.fixture
def owner(user_api_client, auth_test1_p1_user):
    # The project's creator holds the owner role, which grants repo.*.
    client = user_api_client(auth_test1_p1_user)
    return client, sys_uuid.UUID(auth_test1_p1_user.project_id)


class TestRepoAuth:
    def test_realm_reads_need_no_token(self, user_api_noauth_client):
        uri = f"/repo/{sys_uuid.uuid4()}/core/1.0.0/inventory.json"

        assert _auth_status(user_api_noauth_client(), "GET", uri, REALM_ADDR) == 200
        assert _auth_status(user_api_noauth_client(), "HEAD", uri, REALM_ADDR) == 200

    def test_outside_reads_need_a_token(self, user_api_noauth_client):
        uri = f"/repo/{sys_uuid.uuid4()}/core/1.0.0/inventory.json"

        assert _auth_status(user_api_noauth_client(), "GET", uri) == 401

    def test_writes_need_a_token_even_from_the_realm(self, user_api_noauth_client):
        uri = f"/repo/{sys_uuid.uuid4()}/core/1.0.0/inventory.json"

        assert _auth_status(user_api_noauth_client(), "PUT", uri, REALM_ADDR) == 401

    def test_own_project_push_creates_its_repository(self, owner, core_ip):
        client, project_id = owner
        uri = f"/repo/{project_id}/core/1.0.0/inventory.json"

        assert _auth_status(client, "PUT", uri) == 200
        assert _auth_status(client, "GET", uri) == 200

        repo = repo_models.Repository.objects.get_one(
            filters={"uuid": dm_filters.EQ(internal.repository_uuid(project_id))}
        )
        assert repo.project_id == project_id
        assert repo.driver_spec.kind == "internal"
        assert repo.driver_spec.url == f"http://{core_ip}/repo/{project_id}/"
        # A second push reuses it.
        assert _auth_status(client, "MKCOL", f"/repo/{project_id}/x/") == 200

    def test_another_projects_path_is_forbidden(self, owner):
        client, _ = owner
        uri = f"/repo/{sys_uuid.uuid4()}/core/1.0.0/inventory.json"

        assert _auth_status(client, "PUT", uri) == 403
        assert _auth_status(client, "GET", uri) == 403

    def test_traversal_into_another_project_is_forbidden(self, owner):
        client, project_id = owner
        uri = f"/repo/{project_id}/../{sys_uuid.uuid4()}/inventory.json"

        assert _auth_status(client, "PUT", uri) == 403

    @pytest.mark.parametrize("method", ["MOVE", "COPY", "PROPFIND", "POST"])
    def test_other_methods_are_forbidden(self, owner, method):
        client, project_id = owner

        assert _auth_status(client, method, f"/repo/{project_id}/x") == 403
