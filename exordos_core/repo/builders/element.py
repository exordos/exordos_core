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
import typing as tp
import uuid as sys_uuid

from gcl_looper.services.oslo import base as oslo_base
from gcl_sdk.agents.universal.dm import models as ua_models
from gcl_sdk.agents.universal.services import builder as sdk_builder
from restalchemy.dm import filters as ra_filters
from restalchemy.dm import models as ra_models
from restalchemy.dm import properties
from restalchemy.dm import types as ra_types

from exordos_core.repo import exceptions as repo_exceptions
from exordos_core.repo.dm import models

LOG = logging.getLogger(__name__)


class InstalledManifest(
    ua_models.TargetResourceKindAwareMixin,
    ra_models.ModelWithUUID,
    ra_models.ModelWithNameDesc,
    ra_models.ModelWithProject,
):
    """Derivative model representing an installed repo element.

    A derivative of :class:`RepoElement` that exposes the installed element
    to the universal agent. When a repo element is installed, an
    ``InstalledManifest`` is created and pushed to the universal agent, which
    translates it into an Elements Management (EM) Manifest and, after
    installation, links it to the corresponding EM Element.

    The model carries the manifest payload, version, status, and the UUID of
    the runtime EM Element (``element`` field). It is identified by the
    resource kind ``repo_proxy_installed_element``.

    Attributes:
        version: Version string of the installed element.
        manifest: Manifest payload dict (requirements, resources, exports,
            imports, openapi_spec, etc.).
        status: Current status of the installed element
            (see :class:`RepoElementStatus`).
        element: UUID of the runtime EM Element, or ``None`` if not yet
            linked.
    """

    MANIFEST_OPTIONAL_FIELDS = (
        "openapi_spec",
        "exports",
        "imports",
        "requirements",
        "resources",
    )

    version = properties.property(
        ra_types.String(min_length=5, max_length=64), required=True
    )

    manifest = properties.property(
        ra_types.Dict(),
        required=False,
        default=dict,
    )

    status = properties.property(
        ra_types.Enum([s.value for s in models.RepoElementStatus]),
        default=models.RepoElementStatus.NEW.value,
    )

    element = properties.property(
        ra_types.AllowNone(ra_types.UUID()),
        default=None,
    )

    @classmethod
    def get_resource_kind(cls) -> str:
        """Return the resource kind."""
        return "repo_proxy_installed_element"

    def get_resource_target_fields(self) -> tp.Collection[str]:
        """Return the collection of target fields.

        Refer to the Resource model for more details about target fields.
        """
        return frozenset(
            (
                "uuid",
                "name",
                "description",
                "version",
                "manifest",
                "project_id",
            )
        )

    @classmethod
    def from_repo_element(cls, element: models.RepoElement) -> "InstalledManifest":
        uuid = sys_uuid.UUID(str(element.manifest.get("uuid") or element.uuid))
        # Filter out empty optional fields to keep the manifest consistent
        # with _make_installed_manifest in the repo element driver, which
        # also skips empty values. Without this, the agent detects a diff
        # between target and actual resources on every iteration.
        manifest = {
            k: v
            for k, v in element.manifest.items()
            if k not in cls.MANIFEST_OPTIONAL_FIELDS or v
        }
        return cls(
            uuid=uuid,
            name=element.name,
            description=element.manifest.get("description", ""),
            version=element.version,
            manifest=manifest,
            project_id=element.project_id,
        )


class RepoElement(models.RepoElement, ua_models.InstanceWithDerivativesMixin):
    __derivative_model_map__ = {
        "repo_proxy_installed_element": InstalledManifest,
    }

    @classmethod
    def get_resource_kind(cls) -> str:
        return "repo_proxy_element"


class RepoElementBuilderService(
    sdk_builder.UniversalBuilderService,
    oslo_base.OsloConfigurableService,
):
    def __init__(
        self,
        iter_min_period: int = 3,
        iter_pause: float = 0.1,
    ) -> None:
        super().__init__(
            RepoElement,
            iter_min_period=iter_min_period,
            iter_pause=iter_pause,
        )

    def _require_installation(self, instance: RepoElement) -> bool:
        """Check if the element requires installation."""
        return (
            instance.installation_state == models.RepoElementInstallationState.INSTALLED
            and instance.element is None
        )

    def _require_upgrade(self, instance: RepoElement) -> bool:
        """Check if the element requires upgrade."""
        return (
            instance.installation_state == models.RepoElementInstallationState.INSTALLED
            and instance.element is not None
        )

    def _require_release(self, instance: RepoElement) -> bool:
        """Check if the element requires deletion."""
        return (
            instance.installation_state
            == models.RepoElementInstallationState.UNINSTALLED
            and instance.element is not None
        )

    def _require_deletion(self, instance: RepoElement) -> bool:
        """Check if the element requires deletion."""
        return (
            instance.installation_state
            == models.RepoElementInstallationState.UNINSTALLED
            and instance.element is None
        )

    def _get_pending_element(self, instance: RepoElement) -> RepoElement | None:
        """Return the element that is taking the installation over.

        During an upgrade the target version is already marked as installed
        while the old one waits to be released. Returns `None` if there is no
        such element, which means an ordinary deletion.
        """
        pending = models.RepoElement.objects.get_all(
            filters={
                "name": ra_filters.EQ(instance.name),
                "installation_state": ra_filters.EQ(
                    models.RepoElementInstallationState.INSTALLED.value
                ),
            }
        )
        return pending[0] if pending else None

    def _hand_over_deps_bindings(
        self, instance: RepoElement, pending: RepoElement
    ) -> None:
        """Move the dependency bindings to the element being installed.

        The bindings of the released element are dropped: `_install_manifest`
        has already recorded the bindings the pending element really requires,
        which may differ from the ones the released version declared. The
        bindings pointing to the released element are repointed to the pending
        one, because its dependents now rely on the installed version.
        """
        own_bindings = models.RepoElementDepsBinding.objects.get_all(
            filters={"element": ra_filters.EQ(instance.uuid)}
        )
        for binding in own_bindings:
            binding.delete()

        dependents = models.RepoElementDepsBinding.objects.get_all(
            filters={"depends_on": ra_filters.EQ(instance.uuid)}
        )
        for binding in dependents:
            binding.depends_on = pending
            binding.update()

    def _install_manifest(self, instance: RepoElement) -> InstalledManifest:
        """Install the element."""
        LOG.info("Installing element %s:%s", instance.name, instance.version)
        to_install = models.collect_dependencies(instance)

        # Install elements in dependency order
        for element in to_install:
            if models._is_installation_in_progress(element):
                LOG.debug(
                    "Skipping already installed element %s:%s",
                    element.name,
                    element.version,
                )
                continue
            LOG.info("Installing element %s:%s", element.name, element.version)
            element.install()

        # Record dependency bindings for the installed element. These records
        # are not required for the element lifecycle itself, but serve a single
        # purpose: preventing deletion of an element that sits in the middle of
        # a dependency chain. There is no fast way to check whether something
        # still depends on the element being deleted, so this table tracks the
        # relationships and allows the API to decide at deletion time whether
        # the element can be safely removed.
        existing_bindings = models.RepoElementDepsBinding.objects.get_all(
            filters={"element": ra_filters.EQ(instance.uuid)}
        )
        existing_dep_uuids = {b.depends_on.uuid for b in existing_bindings}
        for element in set(to_install[:-1]):
            if element.uuid in existing_dep_uuids:
                continue
            dep_binding = models.RepoElementDepsBinding(
                element=instance,
                depends_on=element,
            )
            dep_binding.save()

        instance.status = models.RepoElementStatus.IN_PROGRESS.value
        LOG.debug(
            "Element %s:%s marked as IN_PROGRESS", instance.name, instance.version
        )
        return InstalledManifest.from_repo_element(instance)

    def _dependencies_available(self, instance: RepoElement) -> bool:
        """Check the dependency closure of the element can be resolved.

        Marks the element as ERROR if the closure cannot be built.
        """
        try:
            models.collect_dependencies(instance)
            LOG.debug(
                "Dependencies validated for element %s:%s",
                instance.name,
                instance.version,
            )
            return True
        except (
            repo_exceptions.DependencyConstraintError,
            repo_exceptions.DependencyConstraintFormatError,
        ) as exc:
            instance.status = models.RepoElementStatus.ERROR.value
            LOG.error(
                "Inappropriate dependencies for element %s:%s: %s",
                instance.name,
                instance.version,
                exc,
            )
            return False
        except repo_exceptions.DependencyNotFoundError as exc:
            instance.status = models.RepoElementStatus.ERROR.value
            LOG.error(
                "Failed to resolve dependencies for element %s:%s: %s",
                instance.name,
                instance.version,
                exc,
            )
            return False

    def can_create_instance_resource(self, instance: RepoElement) -> bool:
        """The hook to check if the instance resource can be created.

        If the hook returns `False`, the instance stays new and the hook is
        called again on the next iteration.
        """
        # An element may already be marked as installed by the time the
        # builder registers it: the installation or upgrade request arrives
        # between the repository sync and this iteration. Such an element is
        # installed right away, so its dependencies must be resolvable first.
        if self._require_installation(instance) or self._require_upgrade(instance):
            return self._dependencies_available(instance)

        return True

    def create_instance_derivatives(
        self, instance: RepoElement
    ) -> tp.Collection[InstalledManifest]:
        """Compute the derivative resources for a new instance.

        The hook is called only for new instances. An element that is already
        installed, or is an upgrade target, when the builder registers it must
        be installed here: the update path is never taken for it, because the
        instance is marked as tracked right after the resource creation.
        """
        if self._require_installation(instance) or self._require_upgrade(instance):
            return [self._install_manifest(instance)]

        return ()

    def post_create_instance_resource(
        self,
        instance: RepoElement,
        resource: ua_models.TargetResource,
        derivatives: tp.Collection[ua_models.TargetResource] = tuple(),
    ) -> None:
        """The hook is performed after saving instance resource.

        The hook is called only for new instances.
        """
        # The installation has already been triggered by
        # `create_instance_derivatives` and the status is set accordingly.
        if derivatives:
            return

        instance.status = models.RepoElementStatus.AVAILABLE.value

    def can_update_instance_resource(
        self, instance: models.RepoElement, resource: ua_models.TargetResource
    ) -> bool:
        """The hook to check if the instance can be updated.

        If the hook returns `False`, the code related to the instance:
        - `update_instance_derivatives`
        - `post_update_instance_resource`
        will be skipped for the current iteration. The
        `can_update_instance_resource` will be called again on the next
        iteration until it returns `True`.
        """
        # Check dependencies are available
        if self._require_installation(instance) or self._require_upgrade(instance):
            # NOTE: dependency collection may be performed again in
            # post_update_instance_resource. This is acceptable for now
            # as it is not a performance bottleneck and can be optimized
            # in the future.
            return self._dependencies_available(instance)

        # NOTE(akremenetsky): During upgrade, the old InstalledManifest must
        # not be deleted before the new one is created. Deleting it first
        # would remove the element from EM entirely, leaving a gap between
        # uninstall and install. The correct order is: create the new
        # derivative first, then delete the old one once the new element is
        # in place.
        if self._require_release(instance):
            # Check if there is a pending repo element with the same name.
            pending = self._get_pending_element(instance)

            # There is no pending element, it means an ordinary deletion
            if not pending:
                LOG.debug(
                    "Ordinary deletion for element %s:%s",
                    instance.name,
                    instance.version,
                )
                return True

            # Check there is an `InstalledManifest` record for the pending element.
            pending_resource = ua_models.Resource.objects.get_one_or_none(
                filters={
                    "uuid": ra_filters.EQ(pending.uuid),
                    "kind": ra_filters.EQ(InstalledManifest.get_resource_kind()),
                }
            )

            # There is no `InstalledManifest` record for the pending element.
            # So wait for it to be created.
            if not pending_resource or pending_resource.value.get("element") is None:
                LOG.debug(
                    "No InstalledManifest record found for pending element %s",
                    pending.uuid,
                )
                return False

        return True

    def update_instance_derivatives(
        self,
        instance: RepoElement,
        resource: ua_models.TargetResource,
        derivative_pairs: tp.Collection[
            tuple[
                ua_models.TargetResourceKindAwareMixin,  # The target resource
                ua_models.TargetResourceKindAwareMixin | None,  # The actual resource
            ]
        ],
    ) -> tp.Collection[InstalledManifest]:
        """Compute the set of derivative resources for the instance.

        Returns the desired collection of InstalledManifest derivatives based
        on the current installation state of the repo element:
        - If the element needs installation or upgrade, triggers the install
          and returns the resulting derivative.
        - If the element is pending deletion (UNINSTALLED with a linked EM
          element), hands the dependency bindings over to the element that
          replaces it, clears the link and returns an empty collection so the
          existing derivative is removed.
        - Otherwise returns the existing target derivatives unchanged.
        """
        # Install new elements
        if self._require_installation(instance) or self._require_upgrade(instance):
            LOG.info(
                "Triggering installation/upgrade for element %s:%s",
                instance.name,
                instance.version,
            )
            return [self._install_manifest(instance)]

        # Release elements after upgrade
        if self._require_release(instance):
            LOG.info("Deleting element %s:%s", instance.name, instance.version)
            if pending := self._get_pending_element(instance):
                self._hand_over_deps_bindings(instance, pending)
            instance.element = None
            instance.status = models.RepoElementStatus.AVAILABLE.value
            return []

        # Delete old elements
        if self._require_deletion(instance) and derivative_pairs:
            LOG.info("Deleting element %s:%s", instance.name, instance.version)
            instance.element = None
            instance.status = models.RepoElementStatus.AVAILABLE.value
            return []

        # Return existing resources
        return [p[0] for p in derivative_pairs]

    def actualize_outdated_instance_derivatives(
        self,
        instance: RepoElement,
        derivative_pairs: tp.Collection[
            tuple[
                InstalledManifest,  # The target resource
                InstalledManifest | None,  # The actual resource
            ]
        ],
    ) -> tp.Collection[InstalledManifest]:
        """Synchronise instance state from the actual installed derivative.

        Called when the actual derivative is outdated relative to the target.
        Reads the linked EM element UUID and status from the first actual
        derivative and propagates them onto the repo element instance, keeping
        the local state consistent with what the universal agent has persisted.
        """
        if derivative_pairs and (manifest := derivative_pairs[0][1]):
            LOG.debug(
                "Synchronizing state for element %s:%s from actual derivative",
                instance.name,
                instance.version,
            )
            instance.element = manifest.element
            instance.status = manifest.status

        return tuple(p[0] for p in derivative_pairs)
