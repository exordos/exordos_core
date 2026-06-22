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
import re
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

_VERSION_RE = re.compile(
    r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?:-(?P<suffix>.+))?$"
)


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
            ("uuid", "name", "description", "version", "manifest", "project_id")
        )

    @classmethod
    def from_repo_element(cls, element: models.RepoElement) -> "InstalledManifest":
        uuid = sys_uuid.UUID(str(element.manifest.get("uuid") or element.uuid))
        return cls(
            uuid=uuid,
            name=element.name,
            description=element.manifest.get("description", ""),
            version=element.version,
            manifest=element.manifest,
            project_id=element.project_id,
        )


class RepoElement(models.RepoElement, ua_models.InstanceWithDerivativesMixin):
    __derivative_model_map__ = {
        "repo_proxy_installed_element": InstalledManifest,
    }

    @classmethod
    def get_resource_kind(cls) -> str:
        return "repo_proxy_element"


def _parse_version(version: str) -> tuple[int, int, int, bool, str]:
    match = _VERSION_RE.match(version)
    if not match:
        raise ValueError(f"Invalid version format: {version}")
    major = int(match.group("major"))
    minor = int(match.group("minor"))
    patch = int(match.group("patch"))
    suffix = match.group("suffix") or ""
    is_release = match.group("suffix") is None
    return (major, minor, patch, is_release, suffix)


def _version_key(version: str) -> tuple[int, int, int, bool, str]:
    return _parse_version(version)


def _version_sort_key(version: str) -> tuple[bool, int, int, int, str]:
    major, minor, patch, is_release, suffix = _parse_version(version)
    return (not is_release, -major, -minor, -patch, suffix)


def _is_installation_in_progress(element: models.RepoElement) -> bool:
    return element.installation_state == models.RepoElementInstallationState.INSTALLED


def _matches_version_constraint(
    element: models.RepoElement,
    constraint: dict,
    name: tp.Optional[str] = None,
) -> bool:
    """Check whether element version satisfies the dependency constraint.

    Supported operators: ==, >, >=, <, <=.
    Release versions are considered greater than development versions
    with the same major.minor.patch base.
    """
    unsupported = set(constraint) - {
        "==",
        ">",
        ">=",
        "<",
        "<=",
    }
    if unsupported:
        raise repo_exceptions.DependencyConstraintFormatError(
            name=name or element.name,
            constraint=constraint,
        )
    version = element.version
    if "==" in constraint:
        return version == constraint["=="]
    try:
        version_key = _version_key(version)
    except ValueError:
        LOG.warning("Invalid version format: %s", version)
        return False
    if ">" in constraint and version_key <= _version_key(constraint[">"]):
        return False
    if ">=" in constraint and version_key < _version_key(constraint[">="]):
        return False
    if "<" in constraint and version_key >= _version_key(constraint["<"]):
        return False
    if "<=" in constraint and version_key > _version_key(constraint["<="]):
        return False
    return True


def _element_sort_key(element: models.RepoElement) -> tuple[bool, int, tuple]:
    """Return sort key for dependency selection.

    Preferred order:
    1. Elements already installed or being installed.
    2. Higher repository priority.
    3. Release versions over development versions.
    4. Higher version number.
    """
    return (
        not _is_installation_in_progress(element),
        -element.repository.priority,
        _version_sort_key(element.version),
    )


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

    def _collect_dependencies(
        self,
        instance: RepoElement,
        visited: set[tuple[str, str]] | None = None,
    ) -> list[RepoElement]:
        """Collect full dependency closure for the element.

        Returns a list of all elements required to install the given element,
        including the requested element itself at the end of the list.
        Installed and in-progress elements are included in the result.
        """
        visited = visited or set()
        key = (instance.name, instance.version)
        if key in visited:
            LOG.debug(
                "Circular dependency detected for %s:%s",
                instance.name,
                instance.version,
            )
            return []
        visited.add(key)

        # When the element is discovered in lazy mode, only its name and
        # version are available. All other artifacts (manifest, specification,
        # inventory, dependencies) must be downloaded before installation can
        # proceed.
        if not instance.manifest:
            LOG.info("Actualizing element %s:%s", instance.name, instance.version)
            instance.repository.actualize_element(instance)

        result: list[RepoElement] = []
        for dep_name, constraint in instance.dependencies.items():
            dep_elements = models.RepoElement.objects.get_all(
                filters={
                    "name": ra_filters.EQ(dep_name),
                }
            )

            # Check if any installed/in-progress element does not satisfy the
            # version constraint. This is a conflict that must be resolved.
            installed_not_matching_candidates = [
                c
                for c in dep_elements
                if _is_installation_in_progress(c)
                and not _matches_version_constraint(c, constraint, name=dep_name)
            ]
            if installed_not_matching_candidates:
                raise repo_exceptions.DependencyConstraintError(
                    name=dep_name,
                    version=installed_not_matching_candidates[0].version,
                    constraint=constraint,
                )

            # All elements that match the constraint
            candidates = [
                c
                for c in dep_elements
                if _matches_version_constraint(c, constraint, name=dep_name)
            ]

            # If no candidates match the constraint, raise an error
            if not candidates:
                LOG.debug(
                    "No candidates found for dependency %s with constraint %s for element %s",
                    dep_name,
                    constraint,
                    instance.name,
                )
                raise repo_exceptions.DependencyNotFoundError(
                    name=dep_name,
                    element=instance.name,
                )

            selected = sorted(candidates, key=_element_sort_key)[0]
            LOG.info(
                "Selected dependency %s:%s for element %s",
                selected.name,
                selected.version,
                instance.name,
            )
            result.extend(self._collect_dependencies(selected, visited))
        result.append(instance)
        return result

    def _install_manifest(self, instance: RepoElement) -> InstalledManifest:
        """Install the element."""
        LOG.info("Installing element %s:%s", instance.name, instance.version)
        to_install = self._collect_dependencies(instance)

        # Install elements in dependency order
        for element in to_install:
            if _is_installation_in_progress(element):
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

    def post_create_instance_resource(
        self,
        instance: RepoElement,
        resource: ua_models.TargetResource,
        derivatives: tp.Collection[ua_models.TargetResource] = tuple(),
    ) -> None:
        """The hook is performed after saving instance resource.

        The hook is called only for new instances.
        """
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
            try:
                # NOTE: dependency collection may be performed again in
                # post_update_instance_resource. This is acceptable for now
                # as it is not a performance bottleneck and can be optimized
                # in the future.
                self._collect_dependencies(instance)
                LOG.debug(
                    "Dependencies validated for element %s:%s",
                    instance.name,
                    instance.version,
                )
                return True
            except repo_exceptions.DependencyConstraintError:
                instance.status = models.RepoElementStatus.ERROR.value
                LOG.error("Inappropriate dependencies for element %s", instance.name)
                return False
            except repo_exceptions.DependencyNotFoundError:
                instance.status = models.RepoElementStatus.ERROR.value
                LOG.error(
                    "Failed to resolve dependencies for element %s", instance.name
                )
                return False

        # NOTE(akremenetsky): During upgrade, the old InstalledManifest must
        # not be deleted before the new one is created. Deleting it first
        # would remove the element from EM entirely, leaving a gap between
        # uninstall and install. The correct order is: create the new
        # derivative first, then delete the old one once the new element is
        # in place.
        if self._require_release(instance):
            # Check if there is a pending repo element with the same name.
            pending = models.RepoElement.objects.get_all(
                filters={
                    "name": ra_filters.EQ(instance.name),
                    "installation_state": ra_filters.EQ(
                        models.RepoElementInstallationState.INSTALLED.value
                    ),
                }
            )

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
                    "uuid": ra_filters.EQ(pending[0].uuid),
                    "kind": ra_filters.EQ(InstalledManifest.get_resource_kind()),
                }
            )

            # There is no `InstalledManifest` record for the pending element.
            # So wait for it to be created.
            if not pending_resource or pending_resource.value.get("element") is None:
                LOG.debug(
                    "No InstalledManifest record found for pending element %s",
                    pending[0].uuid,
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
          element), clears the link and returns an empty collection so the
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
