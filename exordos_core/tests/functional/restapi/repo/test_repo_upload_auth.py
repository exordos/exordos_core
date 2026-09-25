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

"""The check an element repository LB route asks before serving a request.

Requests are shaped like nginx's `auth_request` subrequest: the original
method, `X-Original-Method` / `X-Original-URI` and the caller's token.
"""

import uuid as sys_uuid

from bazooka import exceptions as bazooka_exc
from gcl_iam.tests.functional import clients as iam_clients
import pytest

AUTH_PATH = "repo/upload_auth/repo"


def _ask(client, method, uri):
    url = f"{client.endpoint}{AUTH_PATH}"
    call = {"GET": client.get, "PUT": client.put, "DELETE": client.delete}.get(
        method, client.get
    )
    try:
        return call(
            url, headers={"X-Original-Method": method, "X-Original-URI": uri}
        ).status_code
    except bazooka_exc.BaseHTTPException as e:
        return e.cause.response.status_code


@pytest.fixture
def owner(user_api_client, auth_test1_p1_user):
    # The project's creator holds the owner role, which grants repo.*.
    return user_api_client(auth_test1_p1_user)


@pytest.fixture
def project(auth_test1_p1_user):
    return auth_test1_p1_user.project_id


class TestRepoUploadAuth:
    def test_owner_writes_into_own_project(self, owner, project):
        uri = f"/repo/{project}/app/1.0.0/images/app.raw.zst"

        assert _ask(owner, "PUT", uri) == 204
        assert _ask(owner, "DELETE", f"/repo/{project}/app/1.0.0/") == 204

    def test_write_into_another_project_is_refused(self, owner):
        assert _ask(owner, "PUT", f"/repo/{sys_uuid.uuid4()}/app/1.0.0/x") == 403

    @pytest.mark.parametrize(
        "tail",
        [
            "../{other}/app/x",
            "%2e%2e/{other}/app/x",
            "app/%2e%2e/%2e%2e/{other}/x",
            "app//x",
            "./app/x",
            "app\\..\\x",
        ],
    )
    def test_path_tricks_are_refused(self, owner, project, tail):
        uri = f"/repo/{project}/" + tail.format(other=sys_uuid.uuid4())

        assert _ask(owner, "PUT", uri) == 403

    @pytest.mark.parametrize("method", ["MOVE", "COPY", "MKCOL", "POST"])
    def test_other_methods_are_refused(self, owner, project, method):
        assert _ask(owner, method, f"/repo/{project}/app/x") == 403

    def test_reads_are_open(self, user_api_noauth_client, project):
        anon = user_api_noauth_client()
        uri = f"/repo/{project}/app/1.0.0/inventory.json"

        assert _ask(anon, "GET", uri) == 204
        assert _ask(anon, "HEAD", uri) == 204

    def test_anonymous_write_needs_a_token(self, user_api_noauth_client, project):
        anon = user_api_noauth_client()

        assert _ask(anon, "PUT", f"/repo/{project}/app/x") == 401

    def test_project_member_without_upload_is_refused(
        self,
        user_api_client,
        auth_test2_user,
        project,
    ):
        # A role with no permissions, bound in the owner's project.
        user_api_client(auth_test2_user, permissions=[], project_id=project)
        member = user_api_client(
            iam_clients.GenesisCoreAuth(
                username=auth_test2_user.username,
                password=auth_test2_user.password,
                client_uuid=auth_test2_user.client_uuid,
                client_id=auth_test2_user.client_id,
                client_secret=auth_test2_user.client_secret,
                uuid=auth_test2_user.uuid,
                email=auth_test2_user.email,
                project_id=project,
            )
        )

        assert _ask(member, "PUT", f"/repo/{project}/app/x") == 403

    def test_other_paths_are_left_alone(self, owner):
        # Only /v1/repo/upload_auth... is answered here.
        url = owner.build_collection_uri(["repo", "repositories"])
        assert owner.get(url).status_code == 200
