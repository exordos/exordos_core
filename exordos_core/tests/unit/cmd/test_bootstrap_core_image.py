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

import datetime
import os
from pathlib import Path
from unittest import mock
import uuid

import pytest

from exordos_core.cmd import bootstrap
from exordos_core.compute.builders import pool

IMAGE = "urn:images:7d61b346-1fe2-5528-bf5d-575cb7720778"
SPEC = {"stand": {"bootstraps": [{"image_uri": IMAGE}]}}


@pytest.fixture
def repositories():
    core = mock.Mock(version="0.3.3-dev+20261015195158.72c9d6e0")
    source = mock.Mock(priority=2048)
    source.driver_spec.KIND = "nginx"
    local = mock.Mock(priority=1024)
    local.driver_spec.KIND = "bootstrap"
    with (
        mock.patch.object(bootstrap.repo_models, "Repository") as repository_model,
        mock.patch.object(bootstrap.repo_models, "RepoElement") as element_model,
        mock.patch.object(bootstrap.repo_models, "RepoArtifact") as artifact_model,
    ):
        repository_model.objects.get_all.return_value = [local, source]
        element_model.objects.get_one.return_value = core
        artifact_model.objects.get_all.return_value = []
        yield core, source, local, artifact_model.objects.get_all


def test_registers_image_of_unlisted_dev_version_before_builder_resolution(
    repositories,
):
    core, source, local, artifacts = repositories
    image = mock.Mock(
        urn=IMAGE, uri="https://build.example/core/dev/images/core.raw.zst"
    )
    source.load_driver.return_value.get_element.return_value = (mock.Mock(), [image])

    bootstrap._ensure_bootstrap_core_image(SPEC)

    source.load_driver.return_value.get_element.assert_called_once_with(
        "core", core.version
    )
    source.load_driver.return_value.get_inventory.assert_not_called()
    local.load_driver.assert_not_called()
    image.save.assert_called_once_with()
    assert image.element is core
    assert image.project_id == core.project_id
    artifacts.return_value = [image]
    builder = object.__new__(pool.PoolBuilderService)
    assert builder._resolve_image(IMAGE) == image.uri
    bootstrap._ensure_bootstrap_core_image(SPEC)
    assert image.save.call_count == 1


def test_missing_image_is_retried_instead_of_silently_finishing_bootstrap(repositories):
    _, source, _, _ = repositories
    other = mock.Mock(urn="urn:images:another-version")
    source.load_driver.return_value.get_element.return_value = (mock.Mock(), [other])
    with pytest.raises(RuntimeError, match="unavailable in repositories"):
        bootstrap._ensure_bootstrap_core_image(SPEC)
    other.save.assert_not_called()


def test_unavailable_repository_does_not_prevent_using_another_source(repositories):
    _, source, _, all_artifacts = repositories
    fallback = mock.Mock(priority=1)
    fallback.driver_spec.KIND = "nginx"
    image = mock.Mock(urn=IMAGE)
    fallback.load_driver.return_value.get_element.return_value = (mock.Mock(), [image])
    source.load_driver.return_value.get_element.side_effect = OSError("unavailable")
    bootstrap.repo_models.Repository.objects.get_all.return_value.append(fallback)

    bootstrap._ensure_bootstrap_core_image(SPEC)

    image.save.assert_called_once_with()
    assert all_artifacts.call_count == 1


@pytest.mark.skipif(
    not os.environ.get("EXORDOS_STORAGE_TEST_DB"), reason="Requires test PostgreSQL"
)
def test_bootstrap_image_registration_and_resolution_in_database():
    import gcl_sdk.migrations as sdk_migrations
    import psycopg
    from psycopg import sql
    from restalchemy.storage.sql import engines
    from restalchemy.storage.sql import migrations

    from exordos_core.common import constants
    from exordos_core.repo.dm import models
    from exordos_core.repo.drivers import nginx

    admin_dsn = os.environ["EXORDOS_STORAGE_TEST_DB"]
    database = "bootstrap_image_" + uuid.uuid4().hex
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(
                sql.Identifier(database)
            )
        )
    dsn = admin_dsn.rsplit("/", 1)[0] + "/" + database
    try:
        engines.engine_factory.configure_factory(db_url=dsn)
        for path in [
            Path(sdk_migrations.__file__).parent,
            Path(__file__).parents[4] / "migrations",
        ]:
            engine = migrations.MigrationEngine(str(path))
            engine.apply_migration(engine.get_latest_migration())
        local = models.Repository(
            name=bootstrap.BOOTSTRAP_REPO_NAME,
            project_id=constants.ZERO_UUID,
            driver_spec=models.BootstrapDriverSpec(manifests_dir="/mnt/cdrom"),
        )
        local.insert()
        source = models.Repository(
            name="published-builds",
            project_id=constants.ZERO_UUID,
            driver_spec=models.NginxDriverSpec(
                url="https://build.example/exordos-elements/"
            ),
        )
        source.insert()
        core = models.RepoElement(
            name="core",
            version="0.3.3-dev+20261015195158.72c9d6e0",
            published_at=datetime.datetime.now(datetime.timezone.utc),
            project_id=constants.ZERO_UUID,
            repository=local,
        )
        core.insert()
        driver = nginx.NginxProxyRepoDriver(source)
        inventory = {
            "name": "core",
            "version": core.version,
            "index": {
                "images": {IMAGE.removeprefix("urn:images:"): "exordos-core.raw.zst"}
            },
        }
        response = mock.Mock(text="name: core\nresources: {}\n")
        with (
            mock.patch.object(models.Repository, "load_driver", return_value=driver),
            mock.patch.object(driver._client, "get", return_value=response),
            mock.patch.object(driver, "_get", return_value=inventory),
        ):
            bootstrap._ensure_bootstrap_core_image(SPEC)
            bootstrap._ensure_bootstrap_core_image(SPEC)
        registered = models.RepoArtifact.objects.get_all()
        assert len(registered) == 1
        assert registered[0].element.uuid == core.uuid
        builder = object.__new__(pool.PoolBuilderService)
        assert builder._resolve_image(IMAGE) == (
            f"https://build.example/exordos-elements/core/{core.version}/images/exordos-core.raw.zst"
        )
    finally:
        engines.engine_factory.destroy_engine()
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(database)
                )
            )
