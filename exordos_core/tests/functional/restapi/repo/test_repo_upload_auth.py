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

Requests are shaped like nginx's `auth_request` subrequest: always a GET,
with the original method and URI in `X-Original-Method` / `X-Original-URI`
and the caller's token.
"""

import uuid as sys_uuid

from bazooka import exceptions as bazooka_exc
from gcl_iam.tests.functional import clients as iam_clients
import pytest
from restalchemy.dm import filters as dm_filters

from exordos_core.common import constants as c
from exordos_core.repo.dm import models as repo_models

REPO_UUID = sys_uuid.UUID("31cebe30-f3a5-4813-a1aa-3b59e31d8d15")
AUTH_PATH = f"repo/repositories/{REPO_UUID}/actions/authorize_upload"


@pytest.fixture(autouse=True)
def realm_repository(user_api):
    repository = repo_models.Repository(
        uuid=REPO_UUID,
        name="realm-repository",
        project_id=c.ZERO_UUID,
        driver_spec=repo_models.NginxDriverSpec(
            url="http://10.40.0.1:8081/repo/exordos-elements/"
        ),
    )
    repository.insert()
    return repository


def _ask(client, method, uri):
    url = f"{client.endpoint}{AUTH_PATH}"
    try:
        return client.get(
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

        assert _ask(owner, "PUT", uri) == 200
        assert _ask(owner, "DELETE", f"/repo/{project}/app/1.0.0/") == 200

    @pytest.mark.parametrize("method", ["GET", "HEAD", "PUT", "DELETE"])
    def test_access_to_another_project_is_refused(self, owner, method):
        assert _ask(owner, method, f"/repo/{sys_uuid.uuid4()}/app/1.0.0/x") == 403

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
    @pytest.mark.parametrize("method", ["GET", "PUT"])
    def test_path_tricks_are_refused(self, owner, project, tail, method):
        uri = f"/repo/{project}/" + tail.format(other=sys_uuid.uuid4())

        assert _ask(owner, method, uri) == 403

    @pytest.mark.parametrize(
        "uri",
        [
            "/a/b/{project}/app/x",  # the project must be the second segment
            "/{project}/app/x",
            "/repo/{project}",
            "repo/{project}/app/x",
        ],
    )
    def test_the_layout_is_prefix_then_project(self, owner, project, uri):
        assert _ask(owner, "PUT", uri.format(project=project)) == 403

    @pytest.mark.parametrize("method", ["MOVE", "COPY", "MKCOL", "POST"])
    def test_other_methods_are_refused(self, owner, project, method):
        assert _ask(owner, method, f"/repo/{project}/app/x") == 403

    def test_anonymous_reads_need_a_token(self, user_api_noauth_client, project):
        anon = user_api_noauth_client()
        uri = f"/repo/{project}/app/1.0.0/inventory.json"

        assert _ask(anon, "GET", uri) == 401
        assert _ask(anon, "HEAD", uri) == 401

    def test_anonymous_write_needs_a_token(self, user_api_noauth_client, project):
        anon = user_api_noauth_client()

        assert _ask(anon, "PUT", f"/repo/{project}/app/x") == 401

    def test_unscoped_admin_writes_into_any_project(
        self, user_api_client, auth_user_admin, project
    ):
        # A project scoped token doesn't carry the admin's permissions.
        admin = user_api_client(auth_user_admin)

        assert _ask(admin, "PUT", f"/repo/{project}/app/x") == 200
        assert _ask(admin, "PUT", f"/repo/{sys_uuid.uuid4()}/app/x") == 200
        assert _ask(admin, "PUT", f"/repo/{project}/../{project}/x") == 403

    def test_unscoped_user_without_upload_is_refused(
        self, user_api_client, auth_test1_user, project
    ):
        user = user_api_client(auth_test1_user)

        assert _ask(user, "PUT", f"/repo/{project}/app/x") == 403

    @pytest.mark.parametrize(
        ("permissions", "method", "expected"),
        [
            ([], "PUT", 403),
            (["repo.repository.upload"], "PUT", 200),
            (["repo.repository.upload"], "GET", 403),
            (["repo.repository.read"], "GET", 200),
            (["repo.repository.read"], "HEAD", 200),
            (["repo.repository.read"], "PUT", 403),
        ],
    )
    def test_project_member_needs_the_method_permission(
        self,
        user_api_client,
        auth_test2_user,
        project,
        permissions,
        method,
        expected,
    ):
        user_api_client(auth_test2_user, permissions=permissions, project_id=project)
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

        assert _ask(member, method, f"/repo/{project}/app/x") == expected

    def test_repository_of_another_project_refuses_writes(
        self, owner, project, realm_repository
    ):
        realm_repository.delete()
        repo_models.Repository(
            uuid=REPO_UUID,
            name=realm_repository.name,
            project_id=sys_uuid.uuid4(),
            driver_spec=realm_repository.driver_spec,
        ).insert()

        assert _ask(owner, "PUT", f"/repo/{project}/app/x") == 403

    def test_missing_repository_is_not_authorized(self, owner, realm_repository):
        realm_repository.delete()

        assert _ask(owner, "GET", "/repo/app/x") == 404


class TestRealmRepositoryRegistration:
    @pytest.mark.parametrize("method", ["GET", "HEAD", "PUT", "DELETE"])
    def test_authorization_does_not_register_repositories(self, owner, project, method):
        assert _ask(owner, method, f"/repo/{project}/app/1.0.0/app.yaml") == 200
        assert (
            repo_models.Repository.objects.get_all(
                filters={"project_id": dm_filters.EQ(sys_uuid.UUID(str(project)))}
            )
            == []
        )
