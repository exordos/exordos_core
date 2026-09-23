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

"""A project scoped user reads the repositories of their project and of the
admin project, which holds the realm's shared ones, but writes only their
own. repo.repository.refresh_all is the one exception: it refreshes a shared
repository too."""

import uuid as sys_uuid

from bazooka import exceptions as bazooka_exc
import pytest

from exordos_core.common import constants as c
from exordos_core.repo.dm import models as repo_models

REPOS = ["repo", "repositories"]


def _repository(project_id, name):
    # driver_spec is unique across repositories.
    uid = sys_uuid.uuid4()
    repo = repo_models.Repository(
        name=f"{name}-{uid}",
        project_id=project_id,
        driver_spec=repo_models.NginxDriverSpec(url=f"http://repo.test/{uid}/"),
        refresh_rate=3600,
    )
    repo.insert()
    return repo


def _status(call, *args, **kwargs):
    try:
        return call(*args, **kwargs).status_code
    except bazooka_exc.BaseHTTPException as e:
        return e.cause.response.status_code


@pytest.fixture
def repos(auth_test1_p1_user):
    return {
        "own": _repository(sys_uuid.UUID(auth_test1_p1_user.project_id), "own"),
        "admin": _repository(c.ZERO_UUID, "admin"),
        "other": _repository(sys_uuid.uuid4(), "other"),
    }


@pytest.fixture
def client(user_api_client, auth_test1_p1_user):
    # The project's creator holds the owner role, which grants repo.*.
    return user_api_client(auth_test1_p1_user)


@pytest.fixture
def refresh_all_client(user_api_client, auth_test1_p1_user):
    # The core manifest binds refresh_all to the owner role. The test
    # database is built from the migrations, which seed no such binding,
    # so the permission is granted here instead.
    # The role is bound in the project the token is scoped to: introspection
    # collects the permissions of that project's role bindings only.
    return user_api_client(
        auth_test1_p1_user,
        permissions=["repo.repository.refresh_all"],
        project_id=auth_test1_p1_user.project_id,
    )


def _uuids(response):
    return {r["uuid"] for r in response.json()}


class TestRepositoryVisibility:
    def test_list_holds_own_and_admin_repositories(self, client, repos):
        seen = _uuids(client.get(client.build_collection_uri(REPOS)))

        assert str(repos["own"].uuid) in seen
        assert str(repos["admin"].uuid) in seen
        assert str(repos["other"].uuid) not in seen

    def test_get_reads_an_admin_repository(self, client, repos):
        url = client.build_resource_uri(REPOS + [str(repos["admin"].uuid)])
        assert _status(client.get, url) == 200

        url = client.build_resource_uri(REPOS + [str(repos["other"].uuid)])
        assert _status(client.get, url) == 404

    def test_filter_by_project_stays_within_the_visible_ones(self, client, repos):
        url = client.build_collection_uri(REPOS)

        seen = _uuids(client.get(url, params={"project_id": str(c.ZERO_UUID)}))
        assert str(repos["admin"].uuid) in seen
        assert str(repos["own"].uuid) not in seen

        other = str(repos["other"].project_id)
        assert _status(client.get, url, params={"project_id": other}) == 403

    @pytest.mark.parametrize("action", ["refresh", "upload"])
    def test_actions_on_an_admin_repository_are_forbidden(self, client, repos, action):
        url = client.build_resource_uri(
            REPOS + [str(repos["admin"].uuid), f"actions/{action}/invoke"]
        )
        body = {}
        if action == "upload":
            body = {
                "element_name": "e",
                "element_version": "1.0.0",
                "manifest": {"name": "e", "version": "1.0.0", "resources": {}},
            }

        assert _status(client.post, url, json=body) == 403

    def test_refresh_all_refreshes_an_admin_repository(self, refresh_all_client, repos):
        url = refresh_all_client.build_resource_uri(
            REPOS + [str(repos["admin"].uuid), "actions/refresh/invoke"]
        )

        assert _status(refresh_all_client.post, url, json={}) == 200

    def test_refresh_all_grants_no_upload(self, refresh_all_client, repos):
        url = refresh_all_client.build_resource_uri(
            REPOS + [str(repos["admin"].uuid), "actions/upload/invoke"]
        )
        body = {
            "element_name": "e",
            "element_version": "1.0.0",
            "manifest": {"name": "e", "version": "1.0.0", "resources": {}},
        }

        assert _status(refresh_all_client.post, url, json=body) == 403

    def test_refresh_all_reaches_no_invisible_repository(
        self, refresh_all_client, repos
    ):
        url = refresh_all_client.build_resource_uri(
            REPOS + [str(repos["other"].uuid), "actions/refresh/invoke"]
        )

        assert _status(refresh_all_client.post, url, json={}) == 404

    def test_own_repository_can_still_be_refreshed(self, client, repos):
        url = client.build_resource_uri(
            REPOS + [str(repos["own"].uuid), "actions/refresh/invoke"]
        )

        assert _status(client.post, url, json={}) == 200

    def test_admin_repository_cannot_be_changed_or_deleted(self, client, repos):
        url = client.build_resource_uri(REPOS + [str(repos["admin"].uuid)])

        assert _status(client.put, url, json={"description": "x"}) == 404
        assert _status(client.delete, url) == 404

    def test_unscoped_admin_still_sees_every_repository(
        self, user_api_client, auth_user_admin, repos
    ):
        client = user_api_client(auth_user_admin)
        seen = _uuids(client.get(client.build_collection_uri(REPOS)))

        assert {str(r.uuid) for r in repos.values()} <= seen
