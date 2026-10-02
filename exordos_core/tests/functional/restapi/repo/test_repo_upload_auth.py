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
from exordos_core.vs.dm import models as vs_models

AUTH_PATH = f"iam/clients/{c.ZERO_UUID}/actions/authorize_repo_upload"


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

    def test_reads_are_open(self, user_api_noauth_client, project):
        anon = user_api_noauth_client()
        uri = f"/repo/{project}/app/1.0.0/inventory.json"

        assert _ask(anon, "GET", uri) == 200
        assert _ask(anon, "HEAD", uri) == 200

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


REPO_URL = "http://10.40.0.1:8081/repo/"


def _realm_repos(project):
    return repo_models.Repository.objects.get_all(
        filters={"project_id": dm_filters.EQ(sys_uuid.UUID(str(project)))}
    )


@pytest.fixture
def realm_repo_url(user_api):
    # What bootstrap sets from the realm spec's `repo_url` on a managed realm.
    vs_models.Variable(
        uuid=c.VAR_REALM_REPO_URL_UUID,
        name="realm_repo_url",
        project_id=c.EM_PROJECT_ID,
        setter=vs_models.SelectorVariableSetter(selector_strategy="latest"),
        value=REPO_URL,
    ).insert()
    return REPO_URL


class TestRealmRepositoryRegistration:
    def test_first_upload_registers_the_project_repository(
        self, owner, project, realm_repo_url
    ):
        assert _ask(owner, "PUT", f"/repo/{project}/app/1.0.0/app.yaml") == 200
        assert _ask(owner, "PUT", f"/repo/{project}/app/1.0.0/inventory.json") == 200

        (repo,) = _realm_repos(project)
        assert repo.name == f"realm-{str(project)[:8]}"
        assert repo.driver_spec.url == f"{REPO_URL}{project}/exordos-elements/"
        assert repo.sync_mode == repo_models.SyncMode.COPY.value

    def test_refused_upload_registers_nothing(
        self, user_api_noauth_client, project, realm_repo_url
    ):
        anon = user_api_noauth_client()

        assert _ask(anon, "PUT", f"/repo/{project}/app/x") == 401
        assert _realm_repos(project) == []

    def test_reads_register_nothing(self, owner, project, realm_repo_url):
        assert _ask(owner, "GET", f"/repo/{project}/app/x") == 200
        assert _realm_repos(project) == []

    def test_realm_without_a_repository_registers_nothing(self, owner, project):
        assert _ask(owner, "PUT", f"/repo/{project}/app/x") == 200
        assert _realm_repos(project) == []

    def test_url_held_by_another_project_is_left_alone(
        self, owner, project, realm_repo_url
    ):
        # Another project registered this project's URL first: the upload
        # still goes through, and the other project's row is not touched.
        other = sys_uuid.uuid4()
        repo_models.Repository(
            name="squatter",
            project_id=other,
            refresh_rate=60,
            sync_mode=repo_models.SyncMode.COPY.value,
            driver_spec=repo_models.NginxDriverSpec(
                url=f"{REPO_URL}{project}/exordos-elements/"
            ),
        ).insert()

        assert _ask(owner, "PUT", f"/repo/{project}/app/x") == 200
        assert _realm_repos(project) == []
        assert [r.name for r in _realm_repos(other)] == ["squatter"]
