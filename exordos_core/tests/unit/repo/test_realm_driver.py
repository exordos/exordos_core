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

from unittest import mock
import uuid

import pytest
import requests

from exordos_core.repo.dm import models
from exordos_core.repo.drivers import nginx
from exordos_core.repo.drivers import realm


def repository():
    return models.Repository.restore_from_simple_view(
        **{
            "name": "realm",
            "project_id": str(uuid.uuid4()),
            "driver_spec": {
                "kind": "realm",
                "url": "https://realm.test/repo/project/exordos-elements/",
                "access_token": "secret",
            },
        }
    )


def test_spec_round_trip_and_redaction():
    spec = repository().driver_spec
    assert isinstance(spec, models.RealmDriverSpec)
    assert spec.inventory_path == spec.url + "inventory.json"
    assert spec.dump_to_simple_view()["access_token"] == "secret"
    spec.sanitize_in_place()
    assert spec.dump_to_simple_view()["access_token"] == "***"


def test_realm_inventory_uses_bearer_auth():
    repo = repository()
    with mock.patch.object(nginx.bazooka, "Client") as client:
        driver = realm.RealmProxyRepoDriver(repo)
        auth = client.call_args.kwargs["auth"]
        request = requests.Request("GET", repo.driver_spec.inventory_path).prepare()
        assert auth(request).headers["Authorization"] == "Bearer secret"
        driver.get_inventory()
        client.return_value.get.assert_called_once_with(repo.driver_spec.inventory_path)
        assert driver.artifact_uri("core", "1.0", "images", "core.qcow2") == (
            repo.driver_spec.url + "core/1.0/images/core.qcow2"
        )


def test_nginx_rejects_realm_kind():
    with pytest.raises(ValueError, match="Unsupported driver spec kind"):
        nginx.NginxProxyRepoDriver(repository())


def test_realm_rejects_non_http_url():
    repo = repository()
    repo.driver_spec.url = "file:///tmp/repo/"
    with pytest.raises(ValueError, match="Unsupported URL scheme"):
        realm.RealmProxyRepoDriver(repo)


@pytest.mark.parametrize(
    "url",
    [
        "https://other.test/repo/",
        "http://realm.test/repo/",
        "https://realm.test:444/repo/",
    ],
)
def test_bearer_auth_rejects_other_origin(url):
    auth = realm._BearerAuth("secret", repository().driver_spec.url)
    request = requests.Request("GET", url).prepare()
    with pytest.raises(ValueError, match="origin"):
        auth(request)
    assert "Authorization" not in request.headers


@pytest.mark.parametrize(
    "name",
    [
        "//other.test/x",
        "https://other.test/x",
        "..",
        "%2e%2e",
        "a/b",
        "a\\b",
        "a?b",
        "a#b",
        "%252f",
        "\ncore",
    ],
)
def test_realm_rejects_unsafe_paths(name):
    with mock.patch.object(nginx.bazooka, "Client") as client:
        driver = realm.RealmProxyRepoDriver(repository())
        with pytest.raises(ValueError, match="path component"):
            driver.get_element(name)
        with pytest.raises(ValueError, match="path component"):
            driver.artifact_uri("core", "1.0", "images", name)
        client.return_value.get.assert_not_called()


@pytest.mark.parametrize("slash", ["", "/"])
def test_realm_normalizes_all_urls(slash):
    repo = repository()
    base = repo.driver_spec.url.rstrip("/")
    repo.driver_spec.url = base + slash
    with mock.patch.object(nginx.bazooka, "Client") as client:
        driver = realm.RealmProxyRepoDriver(repo)
        assert repo.driver_spec.inventory_path == base + "/inventory.json"
        assert driver.artifact_uri("core", "1.0", "images", "core.qcow2") == (
            base + "/core/1.0/images/core.qcow2"
        )
        client.return_value.get.return_value.text = "{}"
        with mock.patch.object(models.RepoElement, "from_inventory") as parse:
            parse.return_value = (mock.Mock(), [])
            driver.get_element("core", "1.0")
        assert client.return_value.get.call_args_list == [
            mock.call(base + "/core/1.0/manifests/core.yaml"),
            mock.call(base + "/core/1.0/inventory.json"),
        ]


def test_refresh_and_response_redaction_preserve_token():
    import json

    from restalchemy.api import contexts
    from restalchemy.api import controllers as ra_controllers
    import webob

    from exordos_core.user_api.repo.api import controllers

    repo = repository()
    controller = object.__new__(controllers.RepositoryController)
    request = webob.Request.blank("/actions/refresh", method="POST")
    request.api_context = contexts.RequestContext(request)
    request.api_context.set_active_method("GET")
    controller._req = request
    controller._ctx_project_id = None
    with (
        mock.patch.object(controllers.RepositoryController, "_enforce"),
        mock.patch.object(
            ra_controllers.BaseResourceControllerPaginated, "get", return_value=repo
        ),
    ):
        loaded = controller.get(repo.uuid)
    packer = controllers.RepositoryPacker(controller.__resource__, request)
    for value in (loaded, [loaded]):
        response = json.loads(packer.pack(value))
        if isinstance(response, list):
            response = response[0]
        assert response["driver_spec"]["access_token"] == "***"
        assert repo.driver_spec.access_token == "secret"
    with mock.patch.object(models.Repository, "update") as update:
        loaded.refresh()
        update.assert_called_once()
        assert loaded.driver_spec.access_token == "secret"


def test_bearer_auth_accepts_default_port():
    auth = realm._BearerAuth("secret", "https://realm.test/repo/")
    request = requests.Request("GET", "https://realm.test:443/repo/").prepare()
    assert auth(request).headers["Authorization"] == "Bearer secret"


@pytest.mark.parametrize(
    "url",
    [
        "https://other.test/repo/",
        "http://realm.test/repo/",
        "https://realm.test:444/repo/",
    ],
)
def test_cross_origin_redirect_strips_bearer_auth(url):
    original = requests.Request("GET", "https://realm.test/repo/").prepare()
    realm._BearerAuth("secret", original.url)(original)
    redirected = original.copy()
    redirected.url = url
    response = requests.Response()
    response.request = original
    session = requests.Session()
    session.trust_env = False
    session.rebuild_auth(redirected, response)
    assert "Authorization" not in redirected.headers
