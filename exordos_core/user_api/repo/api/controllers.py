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
from packaging.version import InvalidVersion
from packaging.version import parse as parse_version
from restalchemy.api import actions
from restalchemy.api import constants as ra_c
from restalchemy.api import controllers
from restalchemy.api import field_permissions as field_p
from restalchemy.api import resources
from restalchemy.dm import filters as dm_filters

from exordos_core.common import constants as c
from exordos_core.common import exceptions as common_exc
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
        # TODO(slashburygin):upload() saves a stable element with latest=False without invoking the builder recomputation, so first/newer stable uploads can be absent from latest_stable_elements
        self._enforce("upload")
        resource.upload(element_name, element_version, manifest, description)
        return resource


class StoreControllerMixin(iam_controllers.PolicyBasedWithoutProjectController):
    """project scoping shared by the store endpoints."""

    def _project_filters(self):
        """Restrict a read to the projects the caller may see.

        A project scoped caller reads the admin project, which holds the
        shared catalogue, and their own. A caller without a project, an
        anonymous request included, reads the shared catalogue only.
        """
        if not self._ctx_project_id:
            return {"project_id": dm_filters.EQ(c.ZERO_UUID)}

        return {"project_id": dm_filters.In([c.ZERO_UUID, self._ctx_project_id])}


class LatestStableElementsController(
    StoreControllerMixin,
    controllers.BaseResourceControllerPaginated,
):
    __resource__ = resources.ResourceByRAModel(
        models.RepoElement,
        convert_underscore=False,
        hidden_fields=["installation_state"],
    )

    def filter(self, filters, **kwargs):
        elements_by_name = {}
        elements = models.RepoElement.objects.get_all(
            filters={
                "latest": dm_filters.EQ(True),
                "stable": dm_filters.EQ(True),
                **self._project_filters(),
            }
        )
        for element in elements:
            try:
                version = parse_version(element.version)
            except InvalidVersion:
                continue

            current = elements_by_name.get(element.name)
            candidate = (element.repository.priority, version, element)
            if current is None or candidate[:2] > current[:2]:
                elements_by_name[element.name] = candidate

        # The per-name entries are selected here, not by storage, so the
        # requested page has to be cut here as well. Sorted by the marker
        # column the pagination mixin advances through.
        catalogue = sorted(
            (candidate[2] for candidate in elements_by_name.values()),
            key=lambda element: element.uuid,
        )
        return self._paginate(catalogue)

    def _paginate(self, elements):
        """Return the page requested by `page_limit` and `page_marker`."""
        if not self._pagination_limit:
            return elements

        if self._pagination_marker:
            elements = [
                element
                for element in elements
                if element.uuid > self._pagination_marker
            ]
        return elements[: self._pagination_limit]


class StoreProxyController(StoreControllerMixin, controllers.RoutesListController):
    __TARGET_PATH__ = "/v1/repo/store/"

    def filter(self, filters, order_by=None):
        return controllers.RoutesListController.filter(self, filters, order_by=order_by)


class StoreElementController(
    StoreControllerMixin,
    controllers.BaseResourceControllerPaginated,
):
    __resource__ = resources.ResourceByRAModel(
        models.RepoElement,
        convert_underscore=False,
        process_filters=True,
        hidden_fields=["installation_state"],
    )

    def get(self, uuid, **kwargs):
        kwargs.update(self._project_filters())
        return controllers.BaseResourceControllerPaginated.get(
            self, uuid=uuid, **kwargs
        )

    def filter(self, filters, order_by=None):
        filters.update(self._project_filters())
        return controllers.BaseResourceControllerPaginated.filter(
            self, filters, order_by=order_by
        )

    @actions.get
    def stable_versions(self, resource: models.RepoElement):
        elements = models.RepoElement.objects.get_all(
            filters={
                "name": dm_filters.EQ(resource.name),
                "stable": dm_filters.EQ(True),
                "uuid": dm_filters.NE(resource.uuid),
                **self._project_filters(),
            },
            order_by={"version": "desc"},
        )
        return sorted(
            elements,
            key=lambda element: parse_version(element.version),
            reverse=True,
        )


class RepoArtifactController(
    iam_controllers.PolicyBasedController,
    controllers.BaseResourceControllerPaginated,
):
    __policy_service_name__ = "repo"
    __policy_name__ = "artifact"

    __resource__ = resources.ResourceByRAModel(
        models.RepoArtifact,
        convert_underscore=False,
        fields_permissions=field_p.FieldsPermissions(
            default=field_p.Permissions.RW,
            fields={
                "created_at": {ra_c.ALL: field_p.Permissions.RO},
                "updated_at": {ra_c.ALL: field_p.Permissions.RO},
            },
        ),
    )


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
                "installation_state": {ra_c.ALL: field_p.Permissions.RO},
                "created_at": {ra_c.ALL: field_p.Permissions.RO},
                "updated_at": {ra_c.ALL: field_p.Permissions.RO},
            },
        ),
    )

    def get(self, uuid, **kwargs):
        repo_element = super().get(uuid=uuid, **kwargs)

        # Actualize element if manifest is empty (lazy repository)
        if not repo_element.manifest:
            repo_element.repository.actualize_element(repo_element)

        return repo_element

    def delete(self, uuid):
        # TODO(slashburygin):RepoElementController.delete() can delete the current latest without promoting a remaining stable version
        repo_element = self.get(uuid=uuid)
        if repo_element.installation_state == (
            models.RepoElementInstallationState.INSTALLED.value
        ):
            raise common_exc.ValidateException(err="Cannot delete installed element")
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
