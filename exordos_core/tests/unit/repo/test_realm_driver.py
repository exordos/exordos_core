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
