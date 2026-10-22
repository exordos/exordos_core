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

"""Realm repositories are registered from the bootstrap repository list."""

from unittest import mock
import uuid as sys_uuid

import pytest

from exordos_core.cmd import bootstrap
from exordos_core.repo.dm import models as repo_models


@pytest.mark.parametrize(
    "repo_url",
    [
        "http://10.40.0.1:8081/repo/",
        "http://10.40.0.1:8081/repo/project/exordos-elements/",
    ],
)
def test_bootstrap_registers_the_realm_repository_url_unchanged(repo_url):
    with mock.patch.object(bootstrap, "_ensure_repository") as ensure:
        bootstrap._ensure_repositories_from_spec({"repository": [repo_url]})

    ensure.assert_called_once()
    kwargs = ensure.call_args.kwargs
    assert kwargs["driver_spec"].url == repo_url
    assert kwargs["refresh_rate"] == 3600
    assert kwargs["sync_mode"] == repo_models.SyncMode.LAZY.value


def _repository_definition():
    return {
        "uuid": "fc040e42-439d-41cb-b2e7-7b40dca58810",
        "name": "realm-repo",
        "description": "Realm upload repository",
        "project_id": "00000000-0000-0000-0000-000000000000",
        "status": "ACTIVE",
        "priority": 2048,
        "refresh_rate": 0,
        "sync_mode": "lazy",
        "driver_spec": {
            "kind": "nginx",
            "url": "http://10.40.0.1:8081/repo/",
            "username": "upload",
            "password": "secret",
        },
    }


@pytest.mark.parametrize("single_definition", [False, True])
@pytest.mark.parametrize("status,refresh_rate", [("NEW", 60), ("ACTIVE", 0)])
def test_bootstrap_preserves_full_repository_settings(
    single_definition, status, refresh_rate
):
    definition = _repository_definition()
    definition.update(status=status, refresh_rate=refresh_rate)
    with mock.patch.object(bootstrap, "_ensure_repository") as ensure:
        bootstrap._ensure_repositories_from_spec(
            {"repository": definition if single_definition else [definition]}
        )
    kwargs = ensure.call_args.kwargs
    assert kwargs["uuid"] == sys_uuid.UUID(definition["uuid"])
    for field in (
        "name",
        "description",
        "status",
        "priority",
        "refresh_rate",
        "sync_mode",
    ):
        assert kwargs[field] == definition[field]
    assert kwargs["project_id"] == sys_uuid.UUID(definition["project_id"])
    assert kwargs["driver_spec"].dump_to_simple_view() == definition["driver_spec"]


def test_bootstrap_creates_active_repository_without_waiting_for_inventory():
    definition = _repository_definition()
    definition["driver_spec"] = repo_models.NginxDriverSpec.restore_from_simple_view(
        **definition["driver_spec"]
    )
    definition["uuid"] = sys_uuid.UUID(definition["uuid"])
    definition["project_id"] = sys_uuid.UUID(definition["project_id"])
    repository = repo_models.Repository(**definition)
    with (
        mock.patch.object(repo_models.Repository, "objects") as objects,
        mock.patch.object(repo_models.Repository, "save", autospec=True) as save,
        mock.patch.object(bootstrap.time, "sleep") as sleep,
    ):
        objects.get_one_or_none.return_value = None
        objects.get_one.return_value = repository
        result = bootstrap._ensure_repository(**definition)
    saved = save.call_args.args[0]
    assert saved.uuid == repository.uuid
    assert saved.status == "ACTIVE"
    assert saved.refresh_rate == 0
    assert result.uuid == repository.uuid
    sleep.assert_not_called()


def test_bootstrap_reuses_repository_by_uuid_on_retry():
    definition = _repository_definition()
    definition["driver_spec"] = repo_models.NginxDriverSpec.restore_from_simple_view(
        **definition["driver_spec"]
    )
    definition["uuid"] = sys_uuid.UUID(definition["uuid"])
    definition["project_id"] = sys_uuid.UUID(definition["project_id"])
    existing = repo_models.Repository(**definition)
    with (
        mock.patch.object(repo_models.Repository, "objects") as objects,
        mock.patch.object(repo_models.Repository, "save", autospec=True) as save,
    ):
        objects.get_one_or_none.return_value = existing
        objects.get_one.return_value = existing
        assert bootstrap._ensure_repository(**definition) is existing
    assert (
        objects.get_one_or_none.call_args.kwargs["filters"]["uuid"].value
        == existing.uuid
    )
    save.assert_not_called()


def test_bootstrap_creates_new_repository_and_waits_for_activation():
    definition = _repository_definition()
    definition.update(status="NEW", refresh_rate=60)
    active = repo_models.Repository.restore_from_simple_view(**definition)
    active.status = "ACTIVE"
    definition["driver_spec"] = active.driver_spec
    definition["uuid"] = active.uuid
    definition["project_id"] = active.project_id
    pending = repo_models.Repository(**definition)
    with (
        mock.patch.object(repo_models.Repository, "objects") as objects,
        mock.patch.object(repo_models.Repository, "save", autospec=True) as save,
        mock.patch.object(bootstrap.time, "sleep") as sleep,
    ):
        objects.get_one_or_none.return_value = None
        objects.get_one.side_effect = [pending, active]
        result = bootstrap._ensure_repository(**definition)
    saved = save.call_args.args[0]
    assert saved.status == "NEW"
    assert saved.refresh_rate == 60
    assert saved.uuid == active.uuid
    assert result is active
    sleep.assert_called_once()


def test_bootstrap_definition_without_uuid_uses_name_lookup():
    definition = _repository_definition()
    del definition["uuid"]
    with mock.patch.object(bootstrap, "_ensure_repository") as ensure:
        bootstrap._ensure_repositories_from_spec({"repository": [definition]})
    assert ensure.call_args.kwargs["uuid"] is None
    assert ensure.call_args.kwargs["name"] == definition["name"]


def test_bootstrap_accepts_mixed_repository_definitions_and_legacy_urls():
    definition = _repository_definition()
    url = "https://repo.example.com/exordos-elements/"
    with mock.patch.object(bootstrap, "_ensure_repository") as ensure:
        bootstrap._ensure_repositories_from_spec({"repository": [url, definition]})
    assert ensure.call_count == 2
    assert ensure.call_args_list[0].kwargs["driver_spec"].url == url
    assert ensure.call_args_list[1].kwargs["uuid"] == sys_uuid.UUID(definition["uuid"])
