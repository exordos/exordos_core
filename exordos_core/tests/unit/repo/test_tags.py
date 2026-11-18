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
from restalchemy.storage.sql import orm

from exordos_core.elements.dm import models as em
from exordos_core.repo.agents.universal.drivers import repo_element as driver
from exordos_core.repo.builders import element as builder
from exordos_core.repo.dm import models


def make_repository(**kwargs):
    return models.Repository.restore_from_simple_view(
        name="repo",
        project_id=str(uuid.uuid4()),
        driver_spec={"kind": "nginx", "url": "https://repo.example/"},
        **kwargs,
    )


@pytest.mark.parametrize("tags", [[], ["env:prod", "platform"]])
def test_repository_and_repo_element_tags_round_trip(tags):
    repository = make_repository(tags=tags)
    element = models.RepoElement(
        name="app",
        version="1.0.0",
        repository=repository,
        project_id=repository.project_id,
        tags=tags,
    )
    element.published_at = element.created_at
    for instance in (repository, element):
        data = instance.dump_to_simple_view()
        if isinstance(instance, models.RepoElement):
            data["repository"] = repository
        restored = type(instance).restore_from_simple_view(**data)
        assert restored.tags == tags
        assert data["tags"] == tags
        restored.tags = ["updated"]
        assert restored.dump_to_simple_view()["tags"] == ["updated"]


def test_repository_tags_default_to_empty():
    assert make_repository().tags == []


@pytest.mark.parametrize("tags", [[], ["env:prod", "platform"]])
@pytest.mark.parametrize("operation", ["install", "upgrade"])
def test_manifest_tags_survive_repository_installation(monkeypatch, tags, operation):
    repository = make_repository()
    repo_element = models.RepoElement(
        name="app",
        version="2.0.0",
        repository=repository,
        project_id=repository.project_id,
        manifest={"name": "app", "version": "2.0.0", "tags": tags},
    )
    installed = builder.InstalledManifest.from_repo_element(repo_element)
    client = driver.RepoEmBackendClient(tf_storage=mock.MagicMock())
    manifest = client._make_em_manifest(installed)
    old_element = em.Element(name="app", version="1.0.0", tags=["old"])
    monkeypatch.setattr(
        orm.ObjectCollection,
        "get_one_or_none",
        lambda *args, **kwargs: old_element if operation == "upgrade" else None,
    )
    monkeypatch.setattr(em.element_engine, "load_from_database", lambda: None)
    monkeypatch.setattr(em.element_engine, "add_element", lambda element: None)
    monkeypatch.setattr(em.Element, "save", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        em.Manifest, "apply_element", lambda self, element: (self, element)
    )

    _, element = getattr(manifest, operation)()

    assert manifest.tags == tags
    assert element.tags == tags
    actual = client._make_installed_manifest(manifest, element.uuid)
    assert actual.manifest.get("tags", []) == tags
