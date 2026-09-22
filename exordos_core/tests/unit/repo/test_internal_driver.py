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

"""An internal repository's index is built from the per-element inventories
the core LB stored, so concurrent pushes cannot drop each other's entries."""

import json
import uuid as sys_uuid

import pytest

from exordos_core.repo import internal
from exordos_core.repo.dm import models
from exordos_core.repo.drivers import internal as internal_driver
from exordos_core.repo.drivers import nginx

PROJECT = sys_uuid.UUID("00000000-0000-4000-8000-00000000000a")


def _repository(kind=models.InternalDriverSpec):
    return models.Repository(
        name="internal",
        project_id=PROJECT,
        driver_spec=kind(url=f"http://10.20.0.2/repo/{PROJECT}/exordos-elements/"),
    )


@pytest.fixture
def elements_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(internal, "REPO_DIR", str(tmp_path))
    return tmp_path / str(PROJECT) / "exordos-elements"


def _put(elements_dir, name, version, body):
    path = elements_dir / name / version / "inventory.json"
    path.parent.mkdir(parents=True)
    path.write_text(body if isinstance(body, str) else json.dumps(body))


def test_index_holds_every_pushed_version(elements_dir):
    _put(elements_dir, "core", "1.0.0", {"name": "core", "version": "1.0.0"})
    _put(elements_dir, "core", "1.1.0", {"name": "core", "version": "1.1.0"})
    _put(elements_dir, "empty", "0.0.11", {"name": "empty", "version": "0.0.11"})
    _put(elements_dir, "core", "latest", {"name": "core", "version": "1.1.0"})

    inventory = internal_driver.InternalProxyRepoDriver(_repository()).get_inventory()

    assert inventory == {
        "elements": {
            "core": {
                "1.0.0": {"name": "core", "version": "1.0.0"},
                "1.1.0": {"name": "core", "version": "1.1.0"},
            },
            "empty": {"0.0.11": {"name": "empty", "version": "0.0.11"}},
        }
    }


def test_an_unreadable_inventory_is_skipped(elements_dir):
    _put(elements_dir, "core", "1.0.0", "{not json")
    _put(elements_dir, "empty", "0.0.11", {"name": "empty"})

    inventory = internal_driver.InternalProxyRepoDriver(_repository()).get_inventory()

    assert inventory == {"elements": {"empty": {"0.0.11": {"name": "empty"}}}}


def test_nothing_pushed_yet_is_an_empty_index(elements_dir):
    inventory = internal_driver.InternalProxyRepoDriver(_repository()).get_inventory()

    assert inventory == {"elements": {}}


def test_each_driver_takes_only_its_own_kind():
    with pytest.raises(ValueError):
        nginx.NginxProxyRepoDriver(_repository())
    with pytest.raises(ValueError):
        internal_driver.InternalProxyRepoDriver(_repository(models.NginxDriverSpec))
