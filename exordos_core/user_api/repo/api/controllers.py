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

from gcl_iam.api import controllers as iam_controllers
from restalchemy.api import actions
from restalchemy.api import constants as ra_c
from restalchemy.api import controllers
from restalchemy.api import field_permissions as field_p
from restalchemy.api import resources

from exordos_core.repo.dm import models


class RepoProxyController(controllers.RoutesListController):
    __TARGET_PATH__ = "/v1/repo/"


class RepositoryController(
    iam_controllers.PolicyBasedController,
    controllers.BaseResourceControllerPaginated,
):
    __policy_service_name__ = "repo"
    __policy_name__ = "repository"

    __resource__ = resources.ResourceByRAModel(
        models.Repository,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "status": {ra_c.ALL: field_p.Permissions.RO},
                "created_at": {ra_c.ALL: field_p.Permissions.RO},
                "updated_at": {ra_c.ALL: field_p.Permissions.RO},
            },
        ),
        hidden_fields=["next_refresh"],
    )

    def get(self, uuid, **kwargs):
        repository = super().get(uuid=uuid, **kwargs)
        if repository.driver_spec is not None:
            repository.driver_spec.sanitize_in_place()
        return repository

    def filter(self, filters, **kwargs):
        repositories = super().filter(filters, **kwargs)
        for repository in repositories:
            if repository.driver_spec is not None:
                repository.driver_spec.sanitize_in_place()
        return repositories

    @actions.post
    def refresh(self, resource: models.Repository):
        self._enforce("refresh")
        resource.refresh()
        return resource

    @actions.post
    def upload(
        self,
        resource: models.Repository,
        element_name: str,
        element_version: str,
        manifest: dict,
        description: str = "",
    ):
        self._enforce("upload")
        return resource.upload(element_name, element_version, manifest, description)


class RepoElementController(
    iam_controllers.PolicyBasedController,
    controllers.BaseResourceControllerPaginated,
):
    __policy_service_name__ = "repo"
    __policy_name__ = "element"

    __resource__ = resources.ResourceByRAModel(
        models.RepoElement,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "status": {ra_c.ALL: field_p.Permissions.RO},
                "created_at": {ra_c.ALL: field_p.Permissions.RO},
                "updated_at": {ra_c.ALL: field_p.Permissions.RO},
            },
        ),
        hidden_fields=["installation_state"],
    )

    def get(self, uuid, **kwargs):
        repo_element = super().get(uuid=uuid, **kwargs)

        # Actualize element if manifest is empty (lazy repository)
        if not repo_element.manifest:
            repo_element.repository.actualize_element(repo_element)

        return repo_element

    def delete(self, uuid):
        repo_element = self.get(uuid=uuid)
        if repo_element.installation_state == (
            models.RepoElementInstallationState.INSTALLED.value
        ):
            raise ValueError("Cannot delete installed element")
        return super().delete(uuid)

    @actions.post
    def install(self, resource: models.RepoElement):
        self._enforce("install")
        return resource.install()

    @actions.post
    def uninstall(self, resource: models.RepoElement):
        self._enforce("uninstall")
        return resource.uninstall()

    @actions.post
    def upgrade(self, resource: models.RepoElement, target: str):
        self._enforce("upgrade")
        return resource.upgrade(target=target)

    @actions.post
    def edit(self, resource: models.RepoElement, manifest: dict):
        self._enforce("edit")
        resource.edit(manifest)
        return resource
