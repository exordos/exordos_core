#    Copyright 2025 Genesis Corporation.
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

import logging
import os
import typing as tp

from gcl_sdk.agents.universal.clients.backend import db as client
from gcl_sdk.agents.universal.clients.backend import exceptions as backend_exc
from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.agents.universal.drivers import direct
from gcl_sdk.agents.universal.storage import base as storage_base
from gcl_sdk.agents.universal.storage import fs
from restalchemy.dm import filters as ra_filters

from exordos_core.common import constants as c
from exordos_core.common import exceptions
from exordos_core.elements.dm import models as em_models
from exordos_core.repo.builders import element as re_builder
from exordos_core.repo.dm import models

LOG = logging.getLogger(__name__)
KIND = "repo_proxy_installed_element"
DEFAULT_SCHEMA_VERSION = 1
DEFAULT_API_VERSION = "v1"


class RepoEmBackendClient(client.DatabaseBackendClient):
    """Backend client for repo element resources.

    Translates universal agent resource operations into Elements Management
    manifest lifecycle calls. Each installed repo element corresponds to an
    EM Manifest and, after installation, to an EM Element.
    """

    def __init__(
        self,
        tf_storage: storage_base.AbstractTargetFieldsStorage,
        session: tp.Any | None = None,
    ):
        super().__init__(
            model_specs=tuple(),
            tf_storage=tf_storage,
            session=session,
        )

    def _make_installed_manifest(
        self,
        em_manifest: em_models.Manifest,
        em_element: em_models.Element | None,
    ) -> re_builder.InstalledManifest:
        """Build an InstalledManifest from an EM manifest and its counterparts.

        When a matching repo element is found, its manifest payload is used
        instead of the EM manifest payload, so that repo-specific metadata
        is preserved in the installed representation.
        """
        installed_manifest = re_builder.InstalledManifest(
            uuid=em_manifest.uuid,
            manifest={
                "name": em_manifest.name,
                "version": em_manifest.version,
                "api_version": em_manifest.api_version,
                "description": em_manifest.description,
                "schema_version": em_manifest.schema_version,
            },
            name=em_manifest.name,
            version=em_manifest.version,
            description=em_manifest.description,
            element=em_element.uuid if em_element is not None else None,
            status=models.RepoElementStatus.ACTIVE.value,
            project_id=em_manifest.project_id,
        )

        for field in re_builder.InstalledManifest.MANIFEST_OPTIONAL_FIELDS:
            value = getattr(em_manifest, field)
            if value:
                installed_manifest.manifest[field] = value

        return installed_manifest

    def _make_em_manifest(
        self, installed: re_builder.InstalledManifest
    ) -> em_models.Manifest:
        """Build an EM Manifest object from an InstalledManifest.

        Extracts the manifest payload fields (requirements, resources,
        exports, imports, openapi_spec) from the nested manifest dict
        and maps them onto the EM Manifest model.
        """
        return em_models.Manifest(
            uuid=installed.uuid,
            name=installed.name,
            version=installed.version,
            description=installed.manifest.get("description", ""),
            project_id=installed.project_id,
            schema_version=DEFAULT_SCHEMA_VERSION,
            api_version=DEFAULT_API_VERSION,
            requirements=installed.manifest.get("requirements", {}),
            resources=installed.manifest.get("resources", {}),
            exports=installed.manifest.get("exports", {}),
            imports=installed.manifest.get("imports", {}),
            openapi_spec=installed.manifest.get("openapi_spec"),
        )

    def _get(
        self, session: tp.Any, resource: ua_models.Resource
    ) -> re_builder.InstalledManifest:
        """Get the installed element by resource reference."""
        to_get = re_builder.InstalledManifest.from_ua_resource(resource)

        em_manifest = em_models.Manifest.objects.get_one_or_none(
            filters={"uuid": ra_filters.EQ(to_get.uuid)},
            session=session,
        )

        if em_manifest is None:
            raise backend_exc.ResourceNotFound(resource=resource)

        return to_get

    def _create(
        self, session: tp.Any, resource: ua_models.Resource
    ) -> re_builder.InstalledManifest:
        """Install the element described by the resource.

        Converts the resource into an InstalledManifest, persists the
        corresponding EM Manifest and triggers installation. If the element
        is already installed an upgrade is performed instead. The UUID of
        the resulting EM Element is stored in the returned InstalledManifest.
        """
        to_install = re_builder.InstalledManifest.from_ua_resource(resource)

        manifest = self._make_em_manifest(to_install)
        manifest.save(session=session)

        # Try to install the manifest. If the installation fails, it means
        # the element is already installed and we need to upgrade it.
        try:
            manifest, element = manifest.install(session=session)
        except exceptions.ValidateException:
            manifest, element = manifest.upgrade(session=session)

        to_install.element = element.uuid
        to_install.status = models.RepoElementStatus.ACTIVE.value

        return to_install

    def _update(
        self, session: tp.Any, resource: ua_models.Resource
    ) -> re_builder.InstalledManifest:
        """Update the EM Manifest for the installed element.

        Persists an updated EM Manifest and refreshes the linked EM Element
        UUID in the returned InstalledManifest.
        """
        to_install = re_builder.InstalledManifest.from_ua_resource(resource)

        em_manifest: em_models.Manifest = em_models.Manifest.objects.get_one_or_none(
            filters={"uuid": ra_filters.EQ(to_install.uuid)},
            session=session,
        )
        if em_manifest is None:
            raise backend_exc.ResourceNotFound(resource=resource)

        # Update manifest fields
        upgrade_manifest = True
        if em_manifest.description != to_install.manifest.get("description", ""):
            em_manifest.description = to_install.manifest.get("description", "")
            upgrade_manifest = False

        em_manifest.requirements = to_install.manifest.get("requirements", {})
        em_manifest.resources = to_install.manifest.get("resources", {})
        em_manifest.exports = to_install.manifest.get("exports", {})
        em_manifest.imports = to_install.manifest.get("imports", {})
        em_manifest.openapi_spec = to_install.manifest.get("openapi_spec")
        em_manifest.update(session=session)

        if upgrade_manifest:
            em_manifest.upgrade(session=session)

        # Fetch installed EM element
        em_element = em_models.Element.objects.get_one_or_none(
            filters={"name": ra_filters.EQ(to_install.name)},
            session=session,
        )

        to_install.element = em_element.uuid if em_element is not None else None
        to_install.status = models.RepoElementStatus.ACTIVE.value

        return to_install

    def _list(
        self, session: tp.Any, kind: str, **kwargs
    ) -> tp.Collection[re_builder.InstalledManifest]:
        """List all installed elements for the given kind.

        Collects installed repo elements and matches them against EM manifests
        and elements by (name, version). Returns an InstalledManifest for
        each EM manifest, enriched with repo element and EM element data
        when available.
        """
        if kind != KIND:
            raise ValueError(f"Unsupported kind {kind}")

        # NOTE(akremenetsky): Fetching manifests and elements separately is
        # not the most efficient approach. In the future, a dedicated view
        # that joins repo elements with EM manifests and elements in a single
        # query would be preferable. For now, we keep it simple with
        # individual queries.
        em_manifests = {
            (m.name, m.version): m
            for m in em_models.Manifest.objects.get_all(session=session)
        }
        em_elements = {
            (e.name, e.version): e
            for e in em_models.Element.objects.get_all(session=session)
        }

        # Match them by name and version
        installed = []
        for key, em_manifest in em_manifests.items():
            em_element = em_elements.get(key)
            installed.append(self._make_installed_manifest(em_manifest, em_element))

        return installed

    def _delete(self, session: tp.Any, resource: ua_models.Resource) -> None:
        """Uninstall and remove the EM Manifest for the element.

        Looks up the EM Manifest by UUID. If found, triggers uninstallation
        and deletes the manifest record. If the manifest has already been
        removed, the operation is a no-op.
        """
        to_delete = re_builder.InstalledManifest.from_ua_resource(resource)

        em_manifest = em_models.Manifest.objects.get_one_or_none(
            filters={"uuid": ra_filters.EQ(to_delete.uuid)},
            session=session,
        )
        if em_manifest is not None:
            em_manifest.uninstall(session=session)
            em_manifest.delete(session=session)


class RepoElementCapabilityDriver(direct.DirectAgentDriver):
    """Repo element capability driver for the universal agent.

    The driver exposes the `repo_proxy_installed_element` capability and wires a
    backend client that translates repo element resources into Elements
    Management manifests. Target fields are stored locally in a JSON file
    inside the agent work directory.
    """

    TARGET_FIELDS_FILENAME = "repo_elements_target_fields.json"

    def __init__(
        self,
        agent_work_dir: str = os.path.join(c.WORK_DIR, "exordos_core"),
    ):
        storage_path = os.path.join(agent_work_dir, self.TARGET_FIELDS_FILENAME)

        storage = fs.TargetFieldsFileStorage(storage_path)
        repo_em_client = RepoEmBackendClient(tf_storage=storage)

        super().__init__(storage=storage, client=repo_em_client)

    def get_capabilities(self) -> list[str]:
        """Returns a list of capabilities supported by the driver."""
        return [KIND]
